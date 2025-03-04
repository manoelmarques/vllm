# SPDX-License-Identifier: Apache-2.0

# cumem-based pytorch pluggable allocator to implement sleep mode.
# other approaches tried but failed:
# - cuda-python package binding
# - custom libcuda driver ctypes wrapper
# both of them failed because of cuda context mismatch.
# not sure why, they are created from a different context.
# the only successful approach is to call cuda driver API in C.
import dataclasses
from contextlib import contextmanager
import os
from typing import Any, Callable, Dict, Optional, Tuple, Union

import torch
import struct
import ctypes
import io

from vllm.utils import is_pin_memory_available

_CACHE_FILE = "binary_file.bin"

def _copy_from_cuda_to_bytes(scr_ptr: int, size_in_bytes: int) -> bytes:
    # reserve space for 0 termination
    dest_ptr = ctypes.create_string_buffer(size_in_bytes+1)
    libcudart.cudaMemcpy(dest_ptr, scr_ptr, size_in_bytes)
    # do not include 0 termination
    return bytes(dest_ptr[:-1])

def _copy_from_bytes_to_cuda(dest_ptr: int, data: bytes) -> None:
    # reserve space for 0 termination
    scr_ptr = ctypes.create_string_buffer(data,len(data))
    libcudart.cudaMemcpy(dest_ptr, scr_ptr, len(data))

def _write_bytes(data: bytes, binary_file: io.BufferedWriter) -> None:
    # Pack the length as a 4-byte unsigned integer (little-endian)
    data_len = len(data)
    header = struct.pack("<I", data_len)
    binary_file.write(header)
    if data_len > 0:
        binary_file.write(data)

def _read_bytes(binary_file: io.BufferedReader) -> bytes:
    header = binary_file.read(4)
    if not header:
        raise ValueError("Missing header read")

    if len(header) != 4:
        raise ValueError("Incomplete header read")

    data_len = struct.unpack("<I", header)[0]
    if data_len == 0:
        return bytes()

    data = binary_file.read(data_len)

    if len(data) != data_len:
        raise ValueError("Incomplete data read")

    return data

def find_loaded_library(lib_name) -> Optional[str]:
    """
    According to according to https://man7.org/linux/man-pages/man5/proc_pid_maps.5.html,
    the file `/proc/self/maps` contains the memory maps of the process, which includes the
    shared libraries loaded by the process. We can use this file to find the path of the
    a loaded library.
    """ # noqa
    found_line = None
    with open("/proc/self/maps") as f:
        for line in f:
            if lib_name in line:
                found_line = line
                break
    if found_line is None:
        # the library is not loaded in the current process
        return None
    # if lib_name is libcudart, we need to match a line with:
    # address /path/to/libcudart-hash.so.11.0
    start = found_line.index("/")
    path = found_line[start:].strip()
    filename = path.split("/")[-1]
    assert filename.rpartition(".so")[0].startswith(lib_name), \
        f"Unexpected filename: {filename} for library {lib_name}"
    return path


cumem_available = False
try:
    from vllm.cumem_allocator import (init_module, python_create_and_map,
                                      python_unmap_and_release)
    from vllm.distributed.device_communicators.cuda_wrapper import (
        CudaRTLibrary)
    lib_name = find_loaded_library("cumem_allocator")
    libcudart = CudaRTLibrary()
    cumem_available = True
except ModuleNotFoundError:
    # rocm platform does not support cumem allocator
    init_module = None
    python_create_and_map = None
    python_unmap_and_release = None
    CudaRTLibrary = None
    lib_name = None
    libcudart = None

# py_device, py_alignedSize, py_d_mem, py_p_memHandle
HandleType = Tuple[int, int, int, int]


@dataclasses.dataclass
class AllocationData:
    handle: HandleType
    tag: str
    cpu_backup_tensor: Optional[torch.Tensor] = None


def create_and_map(allocation_handle: HandleType) -> None:
    python_create_and_map(*allocation_handle)


def unmap_and_release(allocation_handle: HandleType) -> None:
    python_unmap_and_release(*allocation_handle)


def get_pluggable_allocator(
    python_malloc_fn: Callable[[int],
                               int], python_free_func: Callable[[int, int],
                                                                None]
) -> torch.cuda.memory.CUDAPluggableAllocator:
    init_module(python_malloc_fn, python_free_func)
    new_alloc = torch.cuda.memory.CUDAPluggableAllocator(
        lib_name, 'my_malloc', 'my_free')
    return new_alloc


@contextmanager
def use_memory_pool_with_allocator(
        python_malloc_fn: Callable[[int], int],
        python_free_func: Callable[[int, int], None]) -> None:
    new_alloc = get_pluggable_allocator(python_malloc_fn, python_free_func)
    mem_pool = torch.cuda.memory.MemPool(new_alloc._allocator)
    with torch.cuda.memory.use_mem_pool(mem_pool):
        yield mem_pool, new_alloc


class CuMemAllocator:
    """
    A singleton class that manages a memory pool for CUDA tensors.
    The memory in this pool can be offloaded or discarded when the
    allocator sleeps.

    Inside the `use_memory_pool(tag)` context, all tensors created will
    be allocated in the memory pool, and has the same tag as the
    tag passed to the context.

    When we call `sleep`, all tensors with the specified tag will be
    offloaded to CPU memory, and the rest of the tensors will be discarded.
    When we call `wake_up`, all tensors that are previously offloaded
    will be loaded back to GPU memory, and the rest of the tensors will
    have empty memory.

    Why it needs to be a singleton?
    When allocated tensors are garbage collected, PyTorch will call
    the free callback, which will call the `python_free_callback` method.
    The C-extension uses a global variable to store the function of an
    instance of this class. If we create multiple instances of this class,
    the global variable will be overwritten and the free callback will
    not work as expected.
    """
    instance: "CuMemAllocator" = None
    default_tag: str = "default"

    @staticmethod
    def get_instance() -> "CuMemAllocator":
        """
        CuMemAllocator is a singleton class.
        We cannot call the constructor directly.
        Call this method to get the instance.
        """
        assert cumem_available, "cumem allocator is not available"
        if CuMemAllocator.instance is None:
            CuMemAllocator.instance = CuMemAllocator()
        return CuMemAllocator.instance

    def __init__(self):
        self.pointer_to_data: Dict[int, AllocationData] = {}
        self.current_tag: str = CuMemAllocator.default_tag
        self.allocator_and_pools: Dict[str, Any] = {}

    def python_malloc_callback(self, allocation_handle: HandleType) -> None:
        """
        Internal method to store the allocation data
        when memory is allocated in the memory pool."""
        py_d_mem = allocation_handle[2]
        self.pointer_to_data[py_d_mem] = AllocationData(
            allocation_handle, self.current_tag)
        return

    def python_free_callback(self, ptr: int) -> HandleType:
        """
        Internal method to look up the allocation data
        when memory is freed in the memory pool."""
        data = self.pointer_to_data.pop(ptr)
        if data.cpu_backup_tensor is not None:
            data.cpu_backup_tensor = None
        return data.handle

    def sleep(
            self,
            level: Optional[int] = 1,
            offload_tags: Optional[Union[Tuple[str, ...],
                                         str]] = None) -> None:
        """
        Put the allocator in sleep mode.
        All data in the memory allocation with the specified tag will be
        offloaded to CPU memory, and others will be discarded.

        :param offload_tags: The tags of the memory allocation that will be
            offloaded. The rest of the memory allocation will be discarded.
        """
        if offload_tags is None:
            # by default, allocated tensors are offloaded
            # when the allocator sleeps
            offload_tags = (CuMemAllocator.default_tag, )
        elif isinstance(offload_tags, str):
            offload_tags = (offload_tags, )

        assert isinstance(offload_tags, tuple)

        with open(_CACHE_FILE, 'wb') as binary_file:
            for ptr, data in self.pointer_to_data.items():
                handle = data.handle
                if data.tag in offload_tags:
                    size_in_bytes = handle[1]
                    if level == 1:
                        cpu_backup_tensor = torch.empty(
                            size_in_bytes,
                            dtype=torch.uint8,
                            device='cpu',
                            pin_memory=is_pin_memory_available())
                        cpu_ptr = cpu_backup_tensor.data_ptr()
                        libcudart.cudaMemcpy(cpu_ptr, ptr, size_in_bytes)
                        data.cpu_backup_tensor = cpu_backup_tensor
                    elif level == 3:
                        data = _copy_from_cuda_to_bytes(ptr, size_in_bytes)
                        _write_bytes(data, binary_file)
                else:
                    _write_bytes(bytes(), binary_file)
                unmap_and_release(handle)

    def wake_up(self):
        """
        Wake up the allocator from sleep mode.
        All data that is previously offloaded will be loaded back to GPU
        memory, and the rest of the data will have empty memory."""
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, 'rb') as binary_file:
                for ptr, data in self.pointer_to_data.items():
                    handle = data.handle
                    create_and_map(handle)
                    data = _read_bytes(binary_file)
                    if len(data) > 0:
                        _copy_from_bytes_to_cuda(ptr,data)
        else:
            for ptr, data in self.pointer_to_data.items():
                handle = data.handle
                create_and_map(handle)
                if data.cpu_backup_tensor is not None:
                    cpu_backup_tensor = data.cpu_backup_tensor
                    if cpu_backup_tensor is not None:
                        size_in_bytes = cpu_backup_tensor.numel(
                        ) * cpu_backup_tensor.element_size()
                        cpu_ptr = cpu_backup_tensor.data_ptr()
                        libcudart.cudaMemcpy(ptr, cpu_ptr, size_in_bytes)
                        data.cpu_backup_tensor = None

    @contextmanager
    def use_memory_pool(self, tag: Optional[str] = None):
        """
        A context manager to use the memory pool.
        All memory allocation created inside the context will be allocated
        in the memory pool, and has the specified tag.

        :param tag: The tag of the memory allocation. If None, the default tag
            will be used.
        """
        if tag is None:
            tag = CuMemAllocator.default_tag

        assert isinstance(tag, str)

        old_tag = self.current_tag
        self.current_tag = tag
        with use_memory_pool_with_allocator(self.python_malloc_callback,
                                            self.python_free_callback) as data:
            # start to hit another PyTorch bug in PyTorch 2.6,
            # possibly because of gc-related issue w.r.t. the allocator and
            # the memory pool.
            # to avoid the issue, we keep a reference of the data.
            # see https://github.com/pytorch/pytorch/issues/146431 .
            self.allocator_and_pools[tag] = data
            yield
            # PyTorch's bug, calling torch.cuda.empty_cache() will error
            # when using pluggable allocator, see
            # https://github.com/pytorch/pytorch/issues/145168 .
            # if we have some memory allocated and then freed,
            # the memory will not be released.
            # right now it is fine, because we only use this allocator
            # during weight loading and kv cache creation, where we only
            # allocate memory.
            # TODO: we need to find a way to release the memory,
            # i.e. calling torch.cuda.empty_cache()
            self.current_tag = old_tag

    def get_current_usage(self) -> int:
        """
        Get the total number of bytes allocated in the memory pool.
        """
        sum_bytes: int = 0
        for ptr, data in self.pointer_to_data.items():
            handle = data.handle
            sum_bytes += handle[1]
        return sum_bytes
