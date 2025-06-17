# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

import time

from vllm.logger import init_logger

logger = init_logger(__name__)

start_init = time.perf_counter()

from .data import (DecoderOnlyInputs, EmbedsInputs, EncoderDecoderInputs,
                   ExplicitEncoderDecoderPrompt, ProcessorInputs, PromptType,
                   SingletonInputs, SingletonPrompt, TextPrompt, TokenInputs,
                   TokensPrompt, build_explicit_enc_dec_prompt, embeds_inputs,
                   to_enc_dec_tuple_list, token_inputs, zip_enc_dec_prompts)

elapsed = time.perf_counter() - start_init
logger.debug("#### data import loaded in %.4f secs", elapsed)
start_init = time.perf_counter()

from .registry import (DummyData, InputContext, InputProcessingContext,
                       InputRegistry)

elapsed = time.perf_counter() - start_init
logger.debug("#### registry import loaded in %.4f secs", elapsed)

INPUT_REGISTRY = InputRegistry()
"""
The global [`InputRegistry`][vllm.inputs.registry.InputRegistry] which is used
by [`LLMEngine`][vllm.LLMEngine] to dispatch data processing according to the
target model.
"""

__all__ = [
    "TextPrompt",
    "TokensPrompt",
    "PromptType",
    "SingletonPrompt",
    "ExplicitEncoderDecoderPrompt",
    "TokenInputs",
    "EmbedsInputs",
    "token_inputs",
    "embeds_inputs",
    "DecoderOnlyInputs",
    "EncoderDecoderInputs",
    "ProcessorInputs",
    "SingletonInputs",
    "build_explicit_enc_dec_prompt",
    "to_enc_dec_tuple_list",
    "zip_enc_dec_prompts",
    "INPUT_REGISTRY",
    "DummyData",
    "InputContext",
    "InputProcessingContext",
    "InputRegistry",
]
