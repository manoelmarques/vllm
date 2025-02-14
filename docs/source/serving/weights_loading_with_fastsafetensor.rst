Loading Model weights with fastsafetensors
===================================================================

Using fastsafetensor library enables loading model weights to GPU memory by leveraging GPU direct storage. See https://github.com/foundation-model-stack/fastsafetensors for more details.
For enabling this feature, set the environment variable ``USE_FASTSAFETENSOR`` to ``true``.
For disabling GDS when using the fastsafetensor library, set the environment variable ``FASTSAFETENSOR_NOGDS`` to ``true``.
For enabling fastsafetensor logging, set the environment variable ``FASTSAFETENSOR_DEBUG`` to ``true``.