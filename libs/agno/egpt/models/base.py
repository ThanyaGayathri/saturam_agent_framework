import asyncio
import collections.abc
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path
from time import sleep, time
from types import AsyncGeneratorType, GeneratorType
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    get_args,
)

if TYPE_CHECKING:
    from egpt.compression.manager import CompressionManager
from uuid import uuid4

from pydantic import BaseModel

from egpt.exceptions import (
    AgentRunException,
    ContextWindowExceededError,
    ModelProviderError,
    RetryableModelProviderError,
    RunCancelledException,
)
from egpt.media import Audio, File, Image, Video
from egpt.metrics import MessageMetrics, ModelType, ToolCallMetrics
from egpt.models.message import Citations, Message
from egpt.models.response import ModelResponse, ModelResponseEvent, ToolExecution
from egpt.run.agent import CustomEvent, RunContentEvent, RunOutput, RunOutputEvent
from egpt.run.requirement import RunRequirement
from egpt.run.team import RunContentEvent as TeamRunContentEvent
from egpt.run.team import TeamRunOutput, TeamRunOutputEvent
from egpt.run.workflow import WorkflowRunOutputEvent
from egpt.tools.function import (
    Function,
    FunctionCall,
    FunctionExecutionResult,
    UserFeedbackOption,
    UserFeedbackQuestion,
    UserInputField,
)
from egpt.utils.log import log_debug, log_error, log_info, log_warning
from egpt.utils.timer import Timer
from egpt.utils.tools import get_function_call_for_tool_call, get_function_call_for_tool_execution


@dataclass
class MessageData:
    response_role: Optional[Literal["system", "user", "assistant", "tool"]] = None
    response_content: Any = ""
    response_reasoning_content: Any = ""
    response_redacted_reasoning_content: Any = ""
    response_citations: Optional[Citations] = None
    response_tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    response_audio: Optional[Audio] = None
    response_image: Optional[Image] = None
    response_video: Optional[Video] = None
    response_file: Optional[File] = None

    response_metrics: Optional[MessageMetrics] = None

    # Data from the provider that we might need on subsequent messages
    response_provider_data: Optional[Dict[str, Any]] = None

    extra: Optional[Dict[str, Any]] = None


def _log_messages(messages: List[Message]) -> None:
    """
    Log messages for debugging.
    """
    for m in messages:
        # Don't log metrics for input messages
        m.log(metrics=False)


def _handle_agent_exception(a_exc: AgentRunException, additional_input: Optional[List[Message]] = None) -> None:
    """Handle AgentRunException and collect additional messages."""
    if additional_input is None:
        additional_input = []
    if a_exc.user_message is not None:
        msg = (
            Message(role="user", content=a_exc.user_message)
            if isinstance(a_exc.user_message, str)
            else a_exc.user_message
        )
        additional_input.append(msg)

    if a_exc.agent_message is not None:
        msg = (
            Message(role="assistant", content=a_exc.agent_message)
            if isinstance(a_exc.agent_message, str)
            else a_exc.agent_message
        )
        additional_input.append(msg)

    if a_exc.messages:
        for m in a_exc.messages:
            if isinstance(m, Message):
                additional_input.append(m)
            elif isinstance(m, dict):
                try:
                    additional_input.append(Message(**m))
                except Exception as e:
                    log_warning(f"Failed to convert dict to Message: {str(e)}")

    if a_exc.stop_execution:
        for m in additional_input:
            m.stop_after_tool_call = True


@dataclass
class Model(ABC):
    # ID of the model to use.
    id: str
    # Name for this Model. This is not sent to the Model API.
    name: Optional[str] = None
    # Provider for this Model. This is not sent to the Model API.
    provider: Optional[str] = None
    # Functional role of this model (e.g., MODEL, OUTPUT_MODEL, PARSER_MODEL).
    # Set by the agent during initialization; defaults to MODEL.
    model_type: ModelType = ModelType.MODEL

    # -*- Do not set the following attributes directly -*-
    # -*- Set them on the Agent instead -*-

    # True if the Model supports structured outputs natively (e.g. OpenAI)
    supports_native_structured_outputs: bool = False
    # True if the Model requires a json_schema for structured outputs (e.g. LMStudio)
    supports_json_schema_outputs: bool = False

    # Controls which (if any) function is called by the model.
    # "none" means the model will not call a function and instead generates a message.
    # "auto" means the model can pick between generating a message or calling a function.
    # Specifying a particular function via {"type: "function", "function": {"name": "my_function"}}
    #   forces the model to call that function.
    # "none" is the default when no functions are present. "auto" is the default if functions are present.
    _tool_choice: Optional[Union[str, Dict[str, Any]]] = None

    # System prompt from the model added to the Agent.
    system_prompt: Optional[str] = None
    # Instructions from the model added to the Agent.
    instructions: Optional[List[str]] = None

    # The role of the tool message.
    tool_message_role: str = "tool"
    # The role of the assistant message.
    assistant_message_role: str = "assistant"

    # Cache model responses to avoid redundant API calls during development
    cache_response: bool = False
    cache_ttl: Optional[int] = None
    cache_dir: Optional[str] = None

    # Retry configuration for model provider errors
    # Number of retries to attempt when a ModelProviderError occurs
    retries: int = 0
    # Delay between retries (in seconds)
    delay_between_retries: int = 1
    # Exponential backoff: if True, the delay between retries is doubled each time
    exponential_backoff: bool = False
    # Enable retrying a model invocation once with a guidance message.
    # This is useful for known errors avoidable with extra instructions.
    retry_with_guidance: bool = True
    # Set the number of times to retry the model invocation with guidance.
    retry_with_guidance_limit: int = 1

    # True if the Model is a third-party provider served over the OpenAI API schema (i.e. an
    # OpenAILike subclass). Lets core/orchestration branch on the capability without importing
    # the concrete integrations base class (EGPT-1.4).
    # Appended at the end of the field block on purpose: inserting a dataclass field mid-list
    # shifts the positional __init__ order of every field after it, for Model and all 46
    # provider subclasses. Keep new fields here.
    is_openai_compatible_proxy: bool = False

    def __post_init__(self):
        if self.provider is None and self.name is not None:
            self.provider = f"{self.name} ({self.id})"

    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate the delay before the next retry attempt."""
        if self.exponential_backoff:
            return self.delay_between_retries * (2**attempt)
        return self.delay_between_retries

    @staticmethod
    def classify_error(error: ModelProviderError) -> ModelProviderError:
        """Re-classify a generic ModelProviderError into a specific subclass.

        Delegates to ModelProviderError.classify(). Kept for backwards compatibility.
        """
        return ModelProviderError.classify(error)

    def _is_retryable_error(self, error: ModelProviderError) -> bool:
        """Determine if an error is worth retrying.

        Non-retryable errors include:
        - ContextWindowExceededError (fast path after classify_error)
        - Client errors (400, 401, 403, 404, 413, 422) that won't change on retry
        - Context window/token limit patterns in error message (defense-in-depth)

        Retryable errors include:
        - Rate limit errors (429)
        - Server errors (500, 502, 503, 504)
        - Anything else not explicitly non-retryable
        """
        # Fast path: already classified by classify_error()
        if isinstance(error, ContextWindowExceededError):
            return False

        non_retryable_codes = {400, 401, 403, 404, 413, 422}
        if error.status_code in non_retryable_codes:
            return False

        # Defense-in-depth: catch context window errors even if not pre-classified
        error_msg = str(error.message).lower()
        if any(pattern in error_msg for pattern in ModelProviderError.CONTEXT_WINDOW_PATTERNS):
            return False

        return True

    def _invoke_with_retry(self, **kwargs) -> ModelResponse:
        """
        Invoke the model with retry logic for ModelProviderError.

        This method wraps the invoke() call and retries on ModelProviderError
        with optional exponential backoff.
        """
        last_exception: Optional[ModelProviderError] = None
        retries_with_guidance_count = kwargs.pop("retries_with_guidance_count", 0)

        for attempt in range(self.retries + 1):
            try:
                return self.invoke(**kwargs)
            except ModelProviderError as e:
                last_exception = self.classify_error(e)
                # Check if error is non-retryable
                if not self._is_retryable_error(last_exception):
                    log_error(f"Non-retryable model provider error: {str(e)}")
                    raise last_exception from e
                if attempt < self.retries:
                    delay = self._get_retry_delay(attempt)
                    log_warning(
                        f"Model provider error (attempt {attempt + 1}/{self.retries + 1}): {last_exception}. Retrying in {delay}s...: {e}",
                    )

                    sleep(delay)
                else:
                    if self.retries > 0:
                        log_error(f"Model provider error after {self.retries + 1} attempts: {str(e)}")
            except RetryableModelProviderError as e:
                current_count = retries_with_guidance_count
                if current_count >= self.retry_with_guidance_limit:
                    raise ModelProviderError(
                        message=f"Max retries with guidance reached. Error: {e.original_error}",
                        model_name=self.name,
                        model_id=self.id,
                    )
                kwargs.pop("retry_with_guidance", None)
                kwargs["retries_with_guidance_count"] = current_count + 1

                # Append the guidance message to help the model avoid the error in the next invoke.
                kwargs["messages"].append(Message(role="user", content=e.retry_guidance_message, temporary=True))

                return self._invoke_with_retry(**kwargs, retry_with_guidance=True)

        # If we've exhausted all retries, raise the last exception
        raise last_exception  # type: ignore

    async def _ainvoke_with_retry(self, **kwargs) -> ModelResponse:
        """
        Asynchronously invoke the model with retry logic for ModelProviderError.

        This method wraps the ainvoke() call and retries on ModelProviderError
        with optional exponential backoff.
        """
        last_exception: Optional[ModelProviderError] = None
        retries_with_guidance_count = kwargs.pop("retries_with_guidance_count", 0)

        for attempt in range(self.retries + 1):
            try:
                return await self.ainvoke(**kwargs)
            except ModelProviderError as e:
                last_exception = self.classify_error(e)
                # Check if error is non-retryable
                if not self._is_retryable_error(last_exception):
                    log_error(f"Non-retryable model provider error: {str(e)}")
                    raise last_exception from e
                if attempt < self.retries:
                    delay = self._get_retry_delay(attempt)
                    log_warning(
                        f"Model provider error (attempt {attempt + 1}/{self.retries + 1}): {last_exception}. Retrying in {delay}s...: {e}",
                    )

                    await asyncio.sleep(delay)
                else:
                    if self.retries > 0:
                        log_error(f"Model provider error after {self.retries + 1} attempts: {str(e)}")
            except RetryableModelProviderError as e:
                current_count = retries_with_guidance_count
                if current_count >= self.retry_with_guidance_limit:
                    raise ModelProviderError(
                        message=f"Max retries with guidance reached. Error: {e.original_error}",
                        model_name=self.name,
                        model_id=self.id,
                    )

                kwargs.pop("retry_with_guidance", None)
                kwargs["retries_with_guidance_count"] = current_count + 1

                # Append the guidance message to help the model avoid the error in the next invoke.
                kwargs["messages"].append(Message(role="user", content=e.retry_guidance_message, temporary=True))

                return await self._ainvoke_with_retry(**kwargs, retry_with_guidance=True)

        # If we've exhausted all retries, raise the last exception
        raise last_exception  # type: ignore

    def _invoke_stream_with_retry(self, **kwargs) -> Iterator[ModelResponse]:
        """
        Invoke the model stream with retry logic for ModelProviderError.

        This method wraps the invoke_stream() call and retries on ModelProviderError
        with optional exponential backoff. Note that retries restart the entire stream.
        """
        last_exception: Optional[ModelProviderError] = None
        retries_with_guidance_count = kwargs.pop("retries_with_guidance_count", 0)

        for attempt in range(self.retries + 1):
            try:
                yield from self.invoke_stream(**kwargs)
                return  # Success, exit the retry loop
            except ModelProviderError as e:
                last_exception = self.classify_error(e)
                # Check if error is non-retryable (e.g., context window exceeded, auth errors)
                if not self._is_retryable_error(last_exception):
                    log_error(f"Non-retryable model provider error: {str(e)}")
                    raise last_exception from e
                if attempt < self.retries:
                    delay = self._get_retry_delay(attempt)
                    log_warning(
                        f"Model provider error during stream (attempt {attempt + 1}/{self.retries + 1}): {last_exception}. : {e}"
                        f"Retrying in {delay}s...: {e}",
                    )

                    sleep(delay)
                else:
                    if self.retries > 0:
                        log_error(f"Model provider error after {self.retries + 1} attempts: {str(e)}")
            except RetryableModelProviderError as e:
                current_count = retries_with_guidance_count
                if current_count >= self.retry_with_guidance_limit:
                    raise ModelProviderError(
                        message=f"Max retries with guidance reached. Error: {e.original_error}",
                        model_name=self.name,
                        model_id=self.id,
                    )

                kwargs.pop("retry_with_guidance", None)
                kwargs["retries_with_guidance_count"] = current_count + 1

                # Append the guidance message to help the model avoid the error in the next invoke.
                kwargs["messages"].append(Message(role="user", content=e.retry_guidance_message, temporary=True))

                yield from self._invoke_stream_with_retry(**kwargs, retry_with_guidance=True)
                return  # Success, exit after regeneration

        # If we've exhausted all retries, raise the last exception
        raise last_exception  # type: ignore

    async def _ainvoke_stream_with_retry(self, **kwargs) -> AsyncIterator[ModelResponse]:
        """
        Asynchronously invoke the model stream with retry logic for ModelProviderError.

        This method wraps the ainvoke_stream() call and retries on ModelProviderError
        with optional exponential backoff. Note that retries restart the entire stream.
        """
        last_exception: Optional[ModelProviderError] = None
        retries_with_guidance_count = kwargs.pop("retries_with_guidance_count", 0)

        for attempt in range(self.retries + 1):
            try:
                async for response in self.ainvoke_stream(**kwargs):
                    yield response
                return  # Success, exit the retry loop
            except ModelProviderError as e:
                last_exception = self.classify_error(e)
                # Check if error is non-retryable
                if not self._is_retryable_error(last_exception):
                    log_error(f"Non-retryable model provider error: {str(e)}")
                    raise last_exception from e
                if attempt < self.retries:
                    delay = self._get_retry_delay(attempt)
                    log_warning(
                        f"Model provider error during stream (attempt {attempt + 1}/{self.retries + 1}): {last_exception}. : {e}"
                        f"Retrying in {delay}s...: {e}",
                    )

                    await asyncio.sleep(delay)
                else:
                    if self.retries > 0:
                        log_error(f"Model provider error after {self.retries + 1} attempts: {str(e)}")
            except RetryableModelProviderError as e:
                current_count = retries_with_guidance_count
                if current_count >= self.retry_with_guidance_limit:
                    raise ModelProviderError(
                        message=f"Max retries with guidance reached. Error: {e.original_error}",
                        model_name=self.name,
                        model_id=self.id,
                    )

                kwargs.pop("retry_with_guidance", None)
                kwargs["retries_with_guidance_count"] = current_count + 1

                # Append the guidance message to help the model avoid the error in the next invoke.
                kwargs["messages"].append(Message(role="user", content=e.retry_guidance_message, temporary=True))

                async for response in self._ainvoke_stream_with_retry(**kwargs, retry_with_guidance=True):
                    yield response
                return  # Success, exit after regeneration

        # If we've exhausted all retries, raise the last exception
        raise last_exception  # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        fields = {"name", "id", "provider"}
        _dict = {field: getattr(self, field) for field in fields if getattr(self, field) is not None}
        return _dict

    def _remove_temporary_messages(self, messages: List[Message]) -> None:
        """Remove temporary messages from the given list.

        Args:
            messages: The list of messages to filter (modified in place).
        """
        messages[:] = [m for m in messages if not m.temporary]

    def get_provider(self) -> str:
        return self.provider or self.name or self.__class__.__name__

    def _get_model_cache_key(self, messages: List[Message], stream: bool, **kwargs: Any) -> str:
        """Generate a cache key based on model messages and core parameters."""
        message_data = []
        for msg in messages:
            msg_dict = {
                "role": msg.role,
                "content": msg.content,
            }
            message_data.append(msg_dict)

        # Include tools parameter in cache key
        has_tools = bool(kwargs.get("tools"))

        cache_data = {
            "model_id": self.id,
            "messages": message_data,
            "has_tools": has_tools,
            "response_format": kwargs.get("response_format"),
            "stream": stream,
        }

        def _cache_default(obj: Any) -> Any:
            if isinstance(obj, type):
                return obj.__name__
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        cache_str = json.dumps(cache_data, sort_keys=True, default=_cache_default)
        return md5(cache_str.encode()).hexdigest()

    def _get_model_cache_file_path(self, cache_key: str) -> Path:
        """Get the file path for a cache key."""
        if self.cache_dir:
            cache_dir = Path(self.cache_dir)
        else:
            cache_dir = Path.home() / ".agno" / "cache" / "model_responses"

        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{cache_key}.json"

    def _get_cached_model_response(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a cached response if it exists and is not expired."""
        cache_file = self._get_model_cache_file_path(cache_key)

        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r") as f:
                cached_data = json.load(f)

            # Check TTL if set (None means no expiration)
            if self.cache_ttl is not None:
                if time() - cached_data["timestamp"] > self.cache_ttl:
                    return None

            return cached_data
        except Exception:
            return None

    def _save_model_response_to_cache(self, cache_key: str, result: ModelResponse, is_streaming: bool = False) -> None:
        """Save a model response to cache."""
        try:
            cache_file = self._get_model_cache_file_path(cache_key)

            cache_data = {
                "timestamp": int(time()),
                "is_streaming": is_streaming,
                "result": result.to_dict(),
            }
            with open(cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception:
            pass

    def _save_streaming_responses_to_cache(self, cache_key: str, responses: List[ModelResponse]) -> None:
        """Save streaming responses to cache."""
        cache_file = self._get_model_cache_file_path(cache_key)

        cache_data = {
            "timestamp": int(time()),
            "is_streaming": True,
            "streaming_responses": [r.to_dict() for r in responses],
        }

        try:
            with open(cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception:
            pass

    def _model_response_from_cache(self, cached_data: Dict[str, Any]) -> ModelResponse:
        """Reconstruct a ModelResponse from cached data."""
        return ModelResponse.from_dict(cached_data["result"])

    def _streaming_responses_from_cache(self, cached_data: list) -> Iterator[ModelResponse]:
        """Reconstruct streaming responses from cached data."""
        for cached_response in cached_data:
            yield ModelResponse.from_dict(cached_response)

    @abstractmethod
    def invoke(self, *args, **kwargs) -> ModelResponse:
        pass

    @abstractmethod
    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        pass

    @abstractmethod
    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        pass

    @abstractmethod
    def ainvoke_stream(self, *args, **kwargs) -> AsyncIterator[ModelResponse]:
        pass

    @abstractmethod
    def _parse_provider_response(self, response: Any, **kwargs) -> ModelResponse:
        """
        Parse the raw response from the model provider into a ModelResponse.

        Args:
            response: Raw response from the model provider

        Returns:
            ModelResponse: Parsed response data
        """
        pass

    @abstractmethod
    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        """
        Parse the streaming response from the model provider into ModelResponse objects.

        Args:
            response: Raw response chunk from the model provider

        Returns:
            ModelResponse: Parsed response delta
        """
        pass

    @staticmethod
    def _tool_name(t: Dict[str, Any]) -> str:
        """Extract a tool's name for deterministic sorting."""
        fn = t.get("function")
        if isinstance(fn, dict):
            return str(fn.get("name", ""))
        return str(t.get("name", ""))

    def _format_tools(self, tools: Optional[List[Union[Function, dict]]]) -> List[Dict[str, Any]]:
        _tool_dicts = []
        for tool in tools or []:
            if isinstance(tool, Function):
                _tool_dicts.append({"type": "function", "function": tool.to_dict()})
            else:
                # If a dict is passed, it is a builtin tool
                _tool_dicts.append(tool)
        # Deterministic ordering so prompt caching gets consistent cache hits.
        # Applies across providers — Anthropic, OpenAI, and Gemini prompt/context
        # caching all require stable request prefixes.
        _tool_dicts.sort(key=self._tool_name)
        return _tool_dicts

    def _ensure_message_metrics_initialized(self, assistant_message: Message) -> None:
        """
        Ensure message metrics are initialized and timer is started.

        Args:
            assistant_message: The assistant message to initialize metrics for
        """
        if assistant_message.metrics is None:
            assistant_message.metrics = MessageMetrics()
        if assistant_message.metrics.timer is None or assistant_message.metrics.timer.start_time is None:
            assistant_message.metrics.start_timer()

    def count_tokens(
        self,
        messages: List[Message],
        tools: Optional[Sequence[Union[Function, Dict[str, Any]]]] = None,
        output_schema: Optional[Union[Dict, Type[BaseModel]]] = None,
    ) -> int:
        from egpt.utils.tokens import count_tokens

        return count_tokens(
            messages,
            tools=list(tools) if tools else None,
            model_id=self.id,
            output_schema=output_schema,
        )

    async def acount_tokens(
        self,
        messages: List[Message],
        tools: Optional[Sequence[Union[Function, Dict[str, Any]]]] = None,
        output_schema: Optional[Union[Dict, Type[BaseModel]]] = None,
    ) -> int:
        return self.count_tokens(messages, tools, output_schema=output_schema)

    def response(
        self,
        messages: List[Message],
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Union[Function, dict]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        tool_call_limit: Optional[int] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        send_media_to_model: bool = True,
        compression_manager: Optional["CompressionManager"] = None,
        after_tool_results: Optional[Callable[["ModelResponse"], None]] = None,
    ) -> ModelResponse:
        """
        Generate a response from the model.

        Args:
            messages: List of messages to send to the model
            response_format: Response format to use
            tools: List of tools to use. This includes the original Function objects and dicts for built-in tools.
            tool_choice: Tool choice to use
            tool_call_limit: Tool call limit
            run_response: Run response to use
            send_media_to_model: Whether to send media to the model
            after_tool_results: Optional callback invoked once per tool batch, after tool result
                messages are appended to ``messages`` and before the next model call (or break).
                Receives the current ``ModelResponse`` (with accumulated ``tool_executions``)
                as its single argument. Used by Agent-level checkpointing
                (``checkpoint="tool-batch"``) to persist mid-run state. Exceptions are caught and
                logged — a failed callback must not kill the run.
        """
        # Check cache if enabled
        if self.cache_response:
            cache_key = self._get_model_cache_key(messages, stream=False, response_format=response_format, tools=tools)
            cached_data = self._get_cached_model_response(cache_key)

            if cached_data:
                log_info("Cache hit for model response")
                return self._model_response_from_cache(cached_data)

        log_debug(f"{self.get_provider()} Response Start", center=True, symbol="-")
        log_debug(f"Model: {self.id}", center=True, symbol="-")

        _log_messages(messages)
        model_response = ModelResponse()

        function_call_count = 0

        _tool_dicts = self._format_tools(tools) if tools is not None else []
        _functions = {tool.name: tool for tool in tools if isinstance(tool, Function)} if tools is not None else {}

        _compress_tool_results = compression_manager is not None and compression_manager.compress_tool_results
        _compression_manager = compression_manager if _compress_tool_results else None

        while True:
            # Compress tool results if compression is enabled and threshold is met
            if _compression_manager is not None and _compression_manager.should_compress(
                messages, tools, model=self, response_format=response_format
            ):
                _compression_manager.compress(
                    messages, run_metrics=run_response.metrics if run_response is not None else None
                )

            # Get response from model
            assistant_message = Message(role=self.assistant_message_role)
            # Initialize message metrics and start timer before model call
            self._ensure_message_metrics_initialized(assistant_message)
            self._process_model_response(
                messages=messages,
                assistant_message=assistant_message,
                model_response=model_response,
                response_format=response_format,
                tools=_tool_dicts,
                tool_choice=tool_choice or self._tool_choice,
                run_response=run_response,
                compress_tool_results=_compress_tool_results,
            )

            # Accumulate metrics for non-stream responses
            if run_response is not None and model_response.response_usage is not None:
                from egpt.metrics import accumulate_model_metrics

                accumulate_model_metrics(model_response, self, self.model_type, run_response.metrics)

            # Add assistant message to messages
            messages.append(assistant_message)

            # Log response and metrics
            assistant_message.log(metrics=True, use_compressed_content=_compress_tool_results)

            # Handle tool calls if present
            if assistant_message.tool_calls:
                # Prepare function calls
                function_calls_to_run = self._prepare_function_calls(
                    assistant_message=assistant_message,
                    messages=messages,
                    model_response=model_response,
                    functions=_functions,
                )
                function_call_results: List[Message] = []

                # Execute function calls
                for function_call_response in self.run_function_calls(
                    function_calls=function_calls_to_run,
                    function_call_results=function_call_results,
                    current_function_call_count=function_call_count,
                    function_call_limit=tool_call_limit,
                ):
                    if isinstance(function_call_response, ModelResponse):
                        # The session state is updated by the function call
                        if function_call_response.updated_session_state is not None:
                            model_response.updated_session_state = function_call_response.updated_session_state

                        # Media artifacts are generated by the function call
                        if function_call_response.images is not None:
                            if model_response.images is None:
                                model_response.images = []
                            model_response.images.extend(function_call_response.images)

                        if function_call_response.audios is not None:
                            if model_response.audios is None:
                                model_response.audios = []
                            model_response.audios.extend(function_call_response.audios)

                        if function_call_response.videos is not None:
                            if model_response.videos is None:
                                model_response.videos = []
                            model_response.videos.extend(function_call_response.videos)

                        if function_call_response.files is not None:
                            if model_response.files is None:
                                model_response.files = []
                            model_response.files.extend(function_call_response.files)

                        if (
                            function_call_response.event
                            in [
                                ModelResponseEvent.tool_call_completed.value,
                                ModelResponseEvent.tool_call_paused.value,
                            ]
                            and function_call_response.tool_executions is not None
                        ):
                            # Record the tool execution in the model response
                            if model_response.tool_executions is None:
                                model_response.tool_executions = []
                            model_response.tool_executions.extend(function_call_response.tool_executions)

                            # If the tool is currently paused (HITL flow), add the requirement to the run response
                            if (
                                function_call_response.event == ModelResponseEvent.tool_call_paused.value
                                and run_response is not None
                            ):
                                current_tool_execution = function_call_response.tool_executions[-1]
                                if run_response.requirements is None:
                                    run_response.requirements = []
                                run_response.requirements.append(RunRequirement(tool_execution=current_tool_execution))

                        elif function_call_response.event not in [
                            ModelResponseEvent.tool_call_started.value,
                            ModelResponseEvent.tool_call_completed.value,
                        ]:
                            if function_call_response.content:
                                model_response.content += function_call_response.content  # type: ignore

                # Add a function call for each successful execution
                function_call_count += len(function_call_results)

                # Format and add results to messages
                self.format_function_call_results(
                    messages=messages,
                    function_call_results=function_call_results,
                    compress_tool_results=_compress_tool_results,
                    **model_response.extra or {},
                )

                if any(msg.images or msg.videos or msg.audio or msg.files for msg in function_call_results):
                    # Handle function call media
                    self._handle_function_call_media(
                        messages=messages,
                        function_call_results=function_call_results,
                        send_media_to_model=send_media_to_model,
                    )

                for function_call_result in function_call_results:
                    function_call_result.log(metrics=True, use_compressed_content=_compress_tool_results)

                # Check if we should stop after tool calls
                if any(m.stop_after_tool_call for m in function_call_results):
                    break

                # Per-turn checkpoint hook: post-gather barrier. Tool results have been
                # appended to messages; fire the hook before deciding whether to loop or break.
                # Failure to checkpoint must not kill a working run — log and continue.
                if after_tool_results is not None:
                    try:
                        after_tool_results(model_response)
                    except Exception as e:
                        log_error(f"after_tool_results callback failed: {e}")

                # If we have any tool calls that require confirmation, break the loop
                if any(tc.requires_confirmation for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require external execution, break the loop
                if any(tc.external_execution_required for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require user input, break the loop
                if any(tc.requires_user_input for tc in model_response.tool_executions or []):
                    break

                # Check if run_response has requirements (e.g., from member agent HITL)
                # This handles cases where a tool (like delegate_task_to_member) propagates
                # HITL requirements from a member agent to the team's run_response
                if run_response is not None and run_response.requirements:
                    if any(not req.is_resolved() for req in run_response.requirements):
                        break

                # Continue loop to get next response
                continue

            # No tool calls or finished processing them
            break

        log_debug(f"{self.get_provider()} Response End", center=True, symbol="-")

        # Save to cache if enabled
        if self.cache_response:
            self._save_model_response_to_cache(cache_key, model_response, is_streaming=False)

        return model_response

    async def aresponse(
        self,
        messages: List[Message],
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Union[Function, dict]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        tool_call_limit: Optional[int] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        send_media_to_model: bool = True,
        compression_manager: Optional["CompressionManager"] = None,
        after_tool_results: Optional[Callable[["ModelResponse"], Awaitable[None]]] = None,
    ) -> ModelResponse:
        """
        Generate an asynchronous response from the model.

        ``after_tool_results``: optional async callback invoked once per tool batch, after tool
        result messages are appended to ``messages`` and before the next model call (or break).
        Receives the current ``ModelResponse`` (with accumulated ``tool_executions``) as its
        single argument. Used by Agent-level checkpointing (``checkpoint="tool-batch"``) to persist
        mid-run state. Exceptions are caught and logged — a failed callback must not kill the run.
        """

        # Check cache if enabled
        if self.cache_response:
            cache_key = self._get_model_cache_key(messages, stream=False, response_format=response_format, tools=tools)
            cached_data = self._get_cached_model_response(cache_key)

            if cached_data:
                log_info("Cache hit for model response")
                return self._model_response_from_cache(cached_data)

        log_debug(f"{self.get_provider()} Async Response Start", center=True, symbol="-")
        log_debug(f"Model: {self.id}", center=True, symbol="-")
        _log_messages(messages)
        model_response = ModelResponse()

        _tool_dicts = self._format_tools(tools) if tools is not None else []
        _functions = {tool.name: tool for tool in tools if isinstance(tool, Function)} if tools is not None else {}

        _compress_tool_results = compression_manager is not None and compression_manager.compress_tool_results
        _compression_manager = compression_manager if _compress_tool_results else None

        function_call_count = 0

        while True:
            # Compress existing tool results BEFORE making API call to avoid context overflow
            if _compression_manager is not None and await _compression_manager.ashould_compress(
                messages, tools, model=self, response_format=response_format
            ):
                await _compression_manager.acompress(
                    messages, run_metrics=run_response.metrics if run_response is not None else None
                )

            # Get response from model
            assistant_message = Message(role=self.assistant_message_role)
            # Initialize message metrics and start timer before model call
            self._ensure_message_metrics_initialized(assistant_message)
            await self._aprocess_model_response(
                messages=messages,
                assistant_message=assistant_message,
                model_response=model_response,
                response_format=response_format,
                tools=_tool_dicts,
                tool_choice=tool_choice or self._tool_choice,
                run_response=run_response,
                compress_tool_results=_compress_tool_results,
            )

            # Accumulate metrics for non-stream responses
            if run_response is not None and model_response.response_usage is not None:
                from egpt.metrics import accumulate_model_metrics

                accumulate_model_metrics(model_response, self, self.model_type, run_response.metrics)

            # Add assistant message to messages
            messages.append(assistant_message)

            # Log response and metrics
            assistant_message.log(metrics=True)

            # Handle tool calls if present
            if assistant_message.tool_calls:
                # Prepare function calls
                function_calls_to_run = self._prepare_function_calls(
                    assistant_message=assistant_message,
                    messages=messages,
                    model_response=model_response,
                    functions=_functions,
                )
                function_call_results: List[Message] = []

                # Execute function calls
                async for function_call_response in self.arun_function_calls(
                    function_calls=function_calls_to_run,
                    function_call_results=function_call_results,
                    current_function_call_count=function_call_count,
                    function_call_limit=tool_call_limit,
                ):
                    if isinstance(function_call_response, ModelResponse):
                        # The session state is updated by the function call
                        if function_call_response.updated_session_state is not None:
                            model_response.updated_session_state = function_call_response.updated_session_state

                        # Media artifacts are generated by the function call
                        if function_call_response.images is not None:
                            if model_response.images is None:
                                model_response.images = []
                            model_response.images.extend(function_call_response.images)

                        if function_call_response.audios is not None:
                            if model_response.audios is None:
                                model_response.audios = []
                            model_response.audios.extend(function_call_response.audios)

                        if function_call_response.videos is not None:
                            if model_response.videos is None:
                                model_response.videos = []
                            model_response.videos.extend(function_call_response.videos)

                        if function_call_response.files is not None:
                            if model_response.files is None:
                                model_response.files = []
                            model_response.files.extend(function_call_response.files)

                        if (
                            function_call_response.event
                            in [
                                ModelResponseEvent.tool_call_completed.value,
                                ModelResponseEvent.tool_call_paused.value,
                            ]
                            and function_call_response.tool_executions is not None
                        ):
                            if model_response.tool_executions is None:
                                model_response.tool_executions = []
                            model_response.tool_executions.extend(function_call_response.tool_executions)

                            # If the tool is currently paused (HITL flow), add the requirement to the run response
                            if (
                                function_call_response.event == ModelResponseEvent.tool_call_paused.value
                                and run_response is not None
                            ):
                                current_tool_execution = function_call_response.tool_executions[-1]
                                if run_response.requirements is None:
                                    run_response.requirements = []
                                run_response.requirements.append(RunRequirement(tool_execution=current_tool_execution))

                        elif function_call_response.event not in [
                            ModelResponseEvent.tool_call_started.value,
                            ModelResponseEvent.tool_call_completed.value,
                        ]:
                            if function_call_response.content:
                                model_response.content += function_call_response.content  # type: ignore

                # Add a function call for each successful execution
                function_call_count += len(function_call_results)

                # Format and add results to messages
                self.format_function_call_results(
                    messages=messages,
                    function_call_results=function_call_results,
                    compress_tool_results=_compress_tool_results,
                    **model_response.extra or {},
                )

                if any(msg.images or msg.videos or msg.audio or msg.files for msg in function_call_results):
                    # Handle function call media
                    self._handle_function_call_media(
                        messages=messages,
                        function_call_results=function_call_results,
                        send_media_to_model=send_media_to_model,
                    )

                for function_call_result in function_call_results:
                    function_call_result.log(metrics=True, use_compressed_content=_compress_tool_results)

                # Check if we should stop after tool calls
                if any(m.stop_after_tool_call for m in function_call_results):
                    break

                # Per-turn checkpoint hook: post-gather barrier. Tool results have been
                # appended to messages; fire the hook before deciding whether to loop or break.
                # Failure to checkpoint must not kill a working run — log and continue.
                if after_tool_results is not None:
                    try:
                        await after_tool_results(model_response)
                    except Exception as e:
                        log_error(f"after_tool_results callback failed: {e}")

                # If we have any tool calls that require confirmation, break the loop
                if any(tc.requires_confirmation for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require external execution, break the loop
                if any(tc.external_execution_required for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require user input, break the loop
                if any(tc.requires_user_input for tc in model_response.tool_executions or []):
                    break

                # Check if run_response has requirements (e.g., from member agent HITL)
                # This handles cases where a tool (like delegate_task_to_member) propagates
                # HITL requirements from a member agent to the team's run_response
                if run_response is not None and run_response.requirements:
                    if any(not req.is_resolved() for req in run_response.requirements):
                        break

                # Continue loop to get next response
                continue

            # No tool calls or finished processing them
            break

        log_debug(f"{self.get_provider()} Async Response End", center=True, symbol="-")

        # Save to cache if enabled
        if self.cache_response:
            self._save_model_response_to_cache(cache_key, model_response, is_streaming=False)

        return model_response

    def _process_model_response(
        self,
        messages: List[Message],
        assistant_message: Message,
        model_response: ModelResponse,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> None:
        """
        Process a single model response and return the assistant message and whether to continue.

        Returns:
            Tuple[Message, bool]: (assistant_message, should_continue)
        """
        # Generate response with retry logic for ModelProviderError
        provider_response = self._invoke_with_retry(
            assistant_message=assistant_message,
            messages=messages,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice or self._tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

        # Set TTFT after response arrives (guard ensures first-call-wins)
        if run_response and run_response.metrics:
            run_response.metrics.set_time_to_first_token()

        # Populate the assistant message
        self._populate_assistant_message(assistant_message=assistant_message, provider_response=provider_response)

        # Update model response with assistant message content and audio
        if assistant_message.content is not None:
            if model_response.content is None:
                model_response.content = assistant_message.get_content_string()
            else:
                model_response.content += assistant_message.get_content_string()
        if assistant_message.reasoning_content is not None:
            model_response.reasoning_content = assistant_message.reasoning_content
        if assistant_message.redacted_reasoning_content is not None:
            model_response.redacted_reasoning_content = assistant_message.redacted_reasoning_content
        if assistant_message.citations is not None:
            model_response.citations = assistant_message.citations
        if assistant_message.audio_output is not None:
            if isinstance(assistant_message.audio_output, Audio):
                model_response.audio = assistant_message.audio_output
        if assistant_message.image_output is not None:
            model_response.images = [assistant_message.image_output]
        if assistant_message.video_output is not None:
            model_response.videos = [assistant_message.video_output]
        if provider_response.extra is not None:
            if model_response.extra is None:
                model_response.extra = {}
            model_response.extra.update(provider_response.extra)
        if provider_response.provider_data is not None:
            model_response.provider_data = provider_response.provider_data
        if provider_response.response_usage is not None:
            model_response.response_usage = provider_response.response_usage
        # Providers (e.g. GeminiInteractions on the agent path) can produce
        # already-executed ToolExecution records server-side; carry them
        # through so run_response.tools / EgptOS UI sees the audit.
        if provider_response.tool_executions:
            if model_response.tool_executions is None:
                model_response.tool_executions = []
            model_response.tool_executions.extend(provider_response.tool_executions)

    async def _aprocess_model_response(
        self,
        messages: List[Message],
        assistant_message: Message,
        model_response: ModelResponse,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> None:
        """
        Process a single async model response and return the assistant message and whether to continue.

        Returns:
            Tuple[Message, bool]: (assistant_message, should_continue)
        """
        # Generate response with retry logic for ModelProviderError
        provider_response = await self._ainvoke_with_retry(
            messages=messages,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice or self._tool_choice,
            assistant_message=assistant_message,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

        # Set TTFT after response arrives (guard ensures first-call-wins)
        if run_response and run_response.metrics:
            run_response.metrics.set_time_to_first_token()

        # Populate the assistant message
        self._populate_assistant_message(assistant_message=assistant_message, provider_response=provider_response)

        # Update model response with assistant message content and audio
        if assistant_message.content is not None:
            if model_response.content is None:
                model_response.content = assistant_message.get_content_string()
            else:
                model_response.content += assistant_message.get_content_string()
        if assistant_message.reasoning_content is not None:
            model_response.reasoning_content = assistant_message.reasoning_content
        if assistant_message.redacted_reasoning_content is not None:
            model_response.redacted_reasoning_content = assistant_message.redacted_reasoning_content
        if assistant_message.citations is not None:
            model_response.citations = assistant_message.citations
        if assistant_message.audio_output is not None:
            if isinstance(assistant_message.audio_output, Audio):
                model_response.audio = assistant_message.audio_output
        if assistant_message.image_output is not None:
            model_response.images = [assistant_message.image_output]
        if assistant_message.video_output is not None:
            model_response.videos = [assistant_message.video_output]
        if provider_response.extra is not None:
            if model_response.extra is None:
                model_response.extra = {}
            model_response.extra.update(provider_response.extra)
        if provider_response.provider_data is not None:
            model_response.provider_data = provider_response.provider_data
        if provider_response.response_usage is not None:
            model_response.response_usage = provider_response.response_usage
        # Providers (e.g. GeminiInteractions on the agent path) can produce
        # already-executed ToolExecution records server-side; carry them
        # through so run_response.tools / EgptOS UI sees the audit.
        if provider_response.tool_executions:
            if model_response.tool_executions is None:
                model_response.tool_executions = []
            model_response.tool_executions.extend(provider_response.tool_executions)

    def _finalize_call_accounting(self, metrics: Any) -> None:
        """Cost and budget settlement for one completed call.

        **One helper called from both response paths**, because they diverged: the first cut wired
        cost (#163) and reconciliation (EGPT-3.6) only into `_populate_assistant_message`, and
        streaming responses go through `_populate_assistant_message_from_stream_data` instead. With
        the agents router defaulting `stream=True`, that left the *dominant* HTTP path with neither
        a populated `MessageMetrics.cost` nor any budget reconciliation. A single entry point is the
        fix for the class of bug, not just this instance.

        Called after the token counts are final. Accounting must never fail a call that otherwise
        succeeded, which is why the reconciliation half is guarded.
        """
        self._apply_cost_to_metrics(metrics)
        try:
            from egpt.providers.budget_runtime import reconcile_reservation

            reconcile_reservation(metrics, model=self)
        except Exception as e:
            log_debug(f"Budget reconciliation unavailable: {e}")

    def _apply_cost_to_metrics(self, metrics: Any) -> None:
        """Populate ``metrics.cost`` from token counts when the provider did not report one.

        Hooked here rather than in each provider: every provider funnels through
        ``_populate_assistant_message``, so this is one edit instead of 46, and a new provider gets
        costing without doing anything.

        **A provider-reported cost is never overwritten.** OpenRouter, Portkey and LiteLLM inject
        the amount they actually charged into the usage payload; that is authoritative in a way a
        computed estimate is not, so computation only fills the gap (aicoe-tracker#163).

        An unpriced model leaves ``cost`` as ``None`` -- a reporting gap, rather than a confidently
        wrong invoice.
        """
        if metrics is None or getattr(metrics, "cost", None) is not None:
            return
        try:
            from egpt.models.pricing import compute_cost
            from egpt.providers.model_provider import BaseModelProvider

            metrics.cost = compute_cost(
                provider_key=BaseModelProvider._registry_key_for(self),
                model_id=getattr(self, "id", None),
                input_tokens=getattr(metrics, "input_tokens", 0) or 0,
                output_tokens=getattr(metrics, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(metrics, "cache_read_tokens", 0) or 0,
                reasoning_tokens=getattr(metrics, "reasoning_tokens", 0) or 0,
            )
        except Exception as e:
            # Costing is reporting, not serving: it must never be able to fail a call that
            # otherwise succeeded. Same principle as the input-token estimate in agent/_run.py.
            log_debug(f"Cost computation unavailable for this call: {e}")

    def _populate_assistant_message(
        self,
        assistant_message: Message,
        provider_response: ModelResponse,
    ) -> Message:
        """
        Populate an assistant message with the provider response data.

        Args:
            assistant_message: The assistant message to populate
            provider_response: Parsed response from the model provider

        Returns:
            Message: The populated assistant message
        """
        if provider_response.role is not None:
            assistant_message.role = provider_response.role

        # Add content to assistant message
        if provider_response.content is not None:
            assistant_message.content = provider_response.content
            # Set time_to_first_token when we receive content if not already set
            if assistant_message.metrics is not None and assistant_message.metrics.time_to_first_token is None:
                assistant_message.metrics.set_time_to_first_token()

        # Add tool calls to assistant message
        if provider_response.tool_calls is not None and len(provider_response.tool_calls) > 0:
            # Ensure every tool call has an id — some providers (e.g. Ollama) omit it
            for tc in provider_response.tool_calls:
                if not tc.get("id"):
                    tc["id"] = str(uuid4())
            assistant_message.tool_calls = provider_response.tool_calls

        # Add audio to assistant message
        if provider_response.audio is not None:
            assistant_message.audio_output = provider_response.audio

        # Add image to assistant message
        if provider_response.images is not None:
            if provider_response.images:
                assistant_message.image_output = provider_response.images[-1]  # Taking last (most recent) image

        # Add video to assistant message
        if provider_response.videos is not None:
            if provider_response.videos:
                assistant_message.video_output = provider_response.videos[-1]  # Taking last (most recent) video

        if provider_response.files is not None:
            if provider_response.files:
                assistant_message.file_output = provider_response.files[-1]  # Taking last (most recent) file

        if provider_response.audios is not None:
            if provider_response.audios:
                assistant_message.audio_output = provider_response.audios[-1]  # Taking last (most recent) audio

        # Add redacted thinking content to assistant message
        if provider_response.redacted_reasoning_content is not None:
            assistant_message.redacted_reasoning_content = provider_response.redacted_reasoning_content

        # Add reasoning content to assistant message
        if provider_response.reasoning_content is not None:
            assistant_message.reasoning_content = provider_response.reasoning_content

        # Add provider data to assistant message
        if provider_response.provider_data is not None:
            assistant_message.provider_data = provider_response.provider_data

        # Add citations to assistant message
        if provider_response.citations is not None:
            assistant_message.citations = provider_response.citations

        # Add usage metrics if provided
        if provider_response.response_usage is not None:
            self._ensure_message_metrics_initialized(assistant_message)
            # Update Metrics with usage data from response
            usage = provider_response.response_usage
            # Use in-place addition to preserve timer automatically
            assistant_message.metrics += usage
            # Set time_to_first_token if we have content and it's not already set
            if provider_response.content is not None and assistant_message.metrics.time_to_first_token is None:
                assistant_message.metrics.set_time_to_first_token()

        # Accounting runs for every completed call, NOT only for calls the vendor reported usage
        # for. Every provider sets `response_usage` under an `if response.usage is not None` guard,
        # so a turn whose usage the vendor omits (or whose parse dropped it) used to skip this block
        # entirely -- and with it, budget settlement for that turn. The reservation then stood at
        # its estimate, under-counting exactly the turns whose cost is least visible. Settling zero
        # tokens for a genuinely usage-less turn is correct; skipping the settle is not.
        if assistant_message.metrics is not None:
            self._finalize_call_accounting(assistant_message.metrics)

        return assistant_message

    def process_response_stream(
        self,
        messages: List[Message],
        assistant_message: Message,
        stream_data: MessageData,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> Iterator[ModelResponse]:
        """
        Process a streaming response from the model with retry logic for ModelProviderError.
        """

        for response_delta in self._invoke_stream_with_retry(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice or self._tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        ):
            # Set TTFT when first chunk arrives (guard ensures first-call-wins)
            if run_response and run_response.metrics:
                run_response.metrics.set_time_to_first_token()
            for model_response_delta in self._populate_stream_data(
                stream_data=stream_data,
                model_response_delta=response_delta,
            ):
                yield model_response_delta

        # Populate assistant message from stream data after the stream ends
        self._populate_assistant_message_from_stream_data(assistant_message=assistant_message, stream_data=stream_data)

    def response_stream(
        self,
        messages: List[Message],
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Union[Function, dict]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        tool_call_limit: Optional[int] = None,
        stream_model_response: bool = True,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        send_media_to_model: bool = True,
        compression_manager: Optional["CompressionManager"] = None,
        after_tool_results: Optional[Callable[["ModelResponse"], None]] = None,
    ) -> Iterator[Union[ModelResponse, RunOutputEvent, TeamRunOutputEvent]]:
        """
        Generate a streaming response from the model.

        ``after_tool_results``: optional callback invoked once per tool batch, after tool result
        messages are appended to ``messages`` and before the next model call (or break). Receives
        the current ``ModelResponse`` (with accumulated ``tool_executions``) as its single
        argument. Used by Agent-level checkpointing (``checkpoint="tool-batch"``) to persist mid-run
        state. Exceptions are caught and logged — a failed callback must not kill the run.
        """
        # Check cache if enabled - capture key BEFORE streaming to avoid mismatch
        cache_key = None
        if self.cache_response:
            cache_key = self._get_model_cache_key(messages, stream=True, response_format=response_format, tools=tools)
            cached_data = self._get_cached_model_response(cache_key)

            if cached_data:
                log_info("Cache hit for streaming model response")
                # Yield cached responses
                for response in self._streaming_responses_from_cache(cached_data["streaming_responses"]):
                    yield response
                return

            log_info("Cache miss for streaming model response")

        # Track streaming responses for caching
        streaming_responses: List[ModelResponse] = []

        log_debug(f"{self.get_provider()} Response Stream Start", center=True, symbol="-")
        log_debug(f"Model: {self.id}", center=True, symbol="-")
        _log_messages(messages)

        _tool_dicts = self._format_tools(tools) if tools is not None else []
        _functions = {tool.name: tool for tool in tools if isinstance(tool, Function)} if tools is not None else {}

        _compress_tool_results = compression_manager is not None and compression_manager.compress_tool_results
        _compression_manager = compression_manager if _compress_tool_results else None

        function_call_count = 0

        while True:
            # Compress existing tool results BEFORE invoke
            if _compression_manager is not None and _compression_manager.should_compress(
                messages, tools, model=self, response_format=response_format
            ):
                # Emit compression started event
                yield ModelResponse(event=ModelResponseEvent.compression_started.value)
                _compression_manager.compress(
                    messages, run_metrics=run_response.metrics if run_response is not None else None
                )
                # Emit compression completed event with stats
                yield ModelResponse(
                    event=ModelResponseEvent.compression_completed.value,
                    compression_stats=_compression_manager.stats.copy(),
                )

            assistant_message = Message(role=self.assistant_message_role)
            # Create assistant message and stream data
            stream_data = MessageData()
            model_response = ModelResponse()

            # Emit LLM request started event
            yield ModelResponse(event=ModelResponseEvent.model_request_started.value)

            if stream_model_response:
                # Initialize message metrics and start timer before model call
                stream_data.response_metrics = MessageMetrics()
                stream_data.response_metrics.start_timer()
                # Initialize assistant_message.metrics for provider invoke_stream calls
                self._ensure_message_metrics_initialized(assistant_message)
                # Generate response
                try:
                    for response in self.process_response_stream(
                        messages=messages,
                        assistant_message=assistant_message,
                        stream_data=stream_data,
                        response_format=response_format,
                        tools=_tool_dicts,
                        tool_choice=tool_choice or self._tool_choice,
                        run_response=run_response,
                        compress_tool_results=_compress_tool_results,
                    ):
                        if self.cache_response and isinstance(response, ModelResponse):
                            streaming_responses.append(response)
                        yield response
                finally:
                    # Accumulate metrics for this streamed iteration (also on cancel)
                    if run_response is not None and assistant_message.metrics is not None:
                        from egpt.metrics import accumulate_model_metrics

                        _stream_model_response = ModelResponse()
                        _stream_model_response.response_usage = assistant_message.metrics
                        accumulate_model_metrics(_stream_model_response, self, self.model_type, run_response.metrics)

            else:
                # Initialize message metrics and start timer before model call
                self._ensure_message_metrics_initialized(assistant_message)
                self._process_model_response(
                    messages=messages,
                    assistant_message=assistant_message,
                    model_response=model_response,
                    response_format=response_format,
                    tools=_tool_dicts,
                    tool_choice=tool_choice or self._tool_choice,
                    run_response=run_response,
                    compress_tool_results=_compress_tool_results,
                )
                # Accumulate metrics for non-streamed response within stream
                if run_response is not None and model_response.response_usage is not None:
                    from egpt.metrics import accumulate_model_metrics

                    accumulate_model_metrics(model_response, self, self.model_type, run_response.metrics)
                if self.cache_response:
                    streaming_responses.append(model_response)
                yield model_response

            # Add assistant message to messages
            messages.append(assistant_message)
            assistant_message.log(metrics=True)

            # Emit LLM request completed event with metrics
            llm_metrics = assistant_message.metrics
            yield ModelResponse(
                event=ModelResponseEvent.model_request_completed.value,
                input_tokens=llm_metrics.input_tokens if llm_metrics else None,
                output_tokens=llm_metrics.output_tokens if llm_metrics else None,
                total_tokens=llm_metrics.total_tokens if llm_metrics else None,
                time_to_first_token=llm_metrics.time_to_first_token if llm_metrics else None,
                reasoning_tokens=llm_metrics.reasoning_tokens if llm_metrics else None,
                cache_read_tokens=llm_metrics.cache_read_tokens if llm_metrics else None,
                cache_write_tokens=llm_metrics.cache_write_tokens if llm_metrics else None,
            )

            # Handle tool calls if present
            if assistant_message.tool_calls is not None:
                # Prepare function calls
                function_calls_to_run: List[FunctionCall] = self.get_function_calls_to_run(
                    assistant_message=assistant_message, messages=messages, functions=_functions
                )
                function_call_results: List[Message] = []

                # Execute function calls
                for function_call_response in self.run_function_calls(
                    function_calls=function_calls_to_run,
                    function_call_results=function_call_results,
                    current_function_call_count=function_call_count,
                    function_call_limit=tool_call_limit,
                ):
                    if self.cache_response and isinstance(function_call_response, ModelResponse):
                        streaming_responses.append(function_call_response)
                    yield function_call_response

                # Add a function call for each successful execution
                function_call_count += len(function_call_results)

                # Format and add results to messages
                if stream_data and stream_data.extra is not None:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                        **stream_data.extra,
                    )
                elif model_response and model_response.extra is not None:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                        **model_response.extra,
                    )
                else:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                    )

                # Handle function call media
                if any(msg.images or msg.videos or msg.audio or msg.files for msg in function_call_results):
                    self._handle_function_call_media(
                        messages=messages,
                        function_call_results=function_call_results,
                        send_media_to_model=send_media_to_model,
                    )

                for function_call_result in function_call_results:
                    function_call_result.log(metrics=True, use_compressed_content=_compress_tool_results)

                # Check if we should stop after tool calls
                if any(m.stop_after_tool_call for m in function_call_results):
                    break

                # Per-turn checkpoint hook: post-gather barrier. Tool results have been
                # appended to messages; fire the hook before deciding whether to loop or break.
                # Failure to checkpoint must not kill a working run — log and continue.
                if after_tool_results is not None:
                    try:
                        after_tool_results(model_response)
                    except Exception as e:
                        log_error(f"after_tool_results callback failed: {e}")

                # If we have any tool calls that require confirmation, break the loop
                if any(fc.function.requires_confirmation for fc in function_calls_to_run):
                    break

                # If we have any tool calls that require external execution, break the loop
                if any(fc.function.external_execution for fc in function_calls_to_run):
                    break

                # If we have any tool calls that require user input, break the loop
                if any(fc.function.requires_user_input for fc in function_calls_to_run):
                    break

                # Check if run_response has requirements (e.g., from member agent HITL)
                # This handles cases where a tool (like delegate_task_to_member) propagates
                # HITL requirements from a member agent to the team's run_response
                if run_response is not None and run_response.requirements:
                    if any(not req.is_resolved() for req in run_response.requirements):
                        break

                # Continue loop to get next response
                continue

            # No tool calls or finished processing them
            break

        log_debug(f"{self.get_provider()} Response Stream End", center=True, symbol="-")

        # Save streaming responses to cache if enabled
        if self.cache_response and cache_key and streaming_responses:
            self._save_streaming_responses_to_cache(cache_key, streaming_responses)

    async def aprocess_response_stream(
        self,
        messages: List[Message],
        assistant_message: Message,
        stream_data: MessageData,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> AsyncIterator[ModelResponse]:
        """
        Process a streaming response from the model with retry logic for ModelProviderError.
        """
        async for response_delta in self._ainvoke_stream_with_retry(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice or self._tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        ):
            # Set TTFT when first chunk arrives (guard ensures first-call-wins)
            if run_response and run_response.metrics:
                run_response.metrics.set_time_to_first_token()
            for model_response_delta in self._populate_stream_data(
                stream_data=stream_data,
                model_response_delta=response_delta,
            ):
                yield model_response_delta

        # Populate assistant message from stream data after the stream ends
        self._populate_assistant_message_from_stream_data(assistant_message=assistant_message, stream_data=stream_data)

    async def aresponse_stream(
        self,
        messages: List[Message],
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Union[Function, dict]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        tool_call_limit: Optional[int] = None,
        stream_model_response: bool = True,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        send_media_to_model: bool = True,
        compression_manager: Optional["CompressionManager"] = None,
        after_tool_results: Optional[Callable[["ModelResponse"], Awaitable[None]]] = None,
    ) -> AsyncIterator[Union[ModelResponse, RunOutputEvent, TeamRunOutputEvent]]:
        """
        Generate an asynchronous streaming response from the model.

        ``after_tool_results``: optional async callback invoked once per tool batch, after tool
        result messages are appended to ``messages`` and before the next model call (or break).
        Receives the current ``ModelResponse`` (with accumulated ``tool_executions``) as its
        single argument. Used by Agent-level checkpointing (``checkpoint="tool-batch"``) to persist
        mid-run state. Exceptions are caught and logged — a failed callback must not kill the run.
        """
        # Check cache if enabled - capture key BEFORE streaming to avoid mismatch
        cache_key = None
        if self.cache_response:
            cache_key = self._get_model_cache_key(messages, stream=True, response_format=response_format, tools=tools)
            cached_data = self._get_cached_model_response(cache_key)

            if cached_data:
                log_info("Cache hit for async streaming model response")
                # Yield cached responses
                for response in self._streaming_responses_from_cache(cached_data["streaming_responses"]):
                    yield response
                return

            log_info("Cache miss for async streaming model response")

        # Track streaming responses for caching
        streaming_responses: List[ModelResponse] = []

        log_debug(f"{self.get_provider()} Async Response Stream Start", center=True, symbol="-")
        log_debug(f"Model: {self.id}", center=True, symbol="-")
        _log_messages(messages)

        _tool_dicts = self._format_tools(tools) if tools is not None else []
        _functions = {tool.name: tool for tool in tools if isinstance(tool, Function)} if tools is not None else {}

        _compress_tool_results = compression_manager is not None and compression_manager.compress_tool_results
        _compression_manager = compression_manager if _compress_tool_results else None

        function_call_count = 0

        while True:
            # Compress existing tool results BEFORE making API call to avoid context overflow
            if _compression_manager is not None and await _compression_manager.ashould_compress(
                messages, tools, model=self, response_format=response_format
            ):
                # Emit compression started event
                yield ModelResponse(event=ModelResponseEvent.compression_started.value)
                await _compression_manager.acompress(
                    messages, run_metrics=run_response.metrics if run_response is not None else None
                )
                # Emit compression completed event with stats
                yield ModelResponse(
                    event=ModelResponseEvent.compression_completed.value,
                    compression_stats=_compression_manager.stats.copy(),
                )

            # Create assistant message and stream data
            assistant_message = Message(role=self.assistant_message_role)
            stream_data = MessageData()
            model_response = ModelResponse()

            # Emit LLM request started event
            yield ModelResponse(event=ModelResponseEvent.model_request_started.value)

            if stream_model_response:
                # Initialize message metrics and start timer before model call
                stream_data.response_metrics = MessageMetrics()
                stream_data.response_metrics.start_timer()
                # Initialize assistant_message.metrics for provider ainvoke_stream calls
                self._ensure_message_metrics_initialized(assistant_message)
                # Generate response
                try:
                    async for model_response_delta in self.aprocess_response_stream(
                        messages=messages,
                        assistant_message=assistant_message,
                        stream_data=stream_data,
                        response_format=response_format,
                        tools=_tool_dicts,
                        tool_choice=tool_choice or self._tool_choice,
                        run_response=run_response,
                        compress_tool_results=_compress_tool_results,
                    ):
                        if self.cache_response and isinstance(model_response_delta, ModelResponse):
                            streaming_responses.append(model_response_delta)
                        yield model_response_delta
                finally:
                    # Accumulate metrics for this streamed iteration (also on cancel)
                    if run_response is not None and assistant_message.metrics is not None:
                        from egpt.metrics import accumulate_model_metrics

                        _stream_model_response = ModelResponse()
                        _stream_model_response.response_usage = assistant_message.metrics
                        accumulate_model_metrics(_stream_model_response, self, self.model_type, run_response.metrics)

            else:
                # Initialize message metrics and start timer before model call
                self._ensure_message_metrics_initialized(assistant_message)
                await self._aprocess_model_response(
                    messages=messages,
                    assistant_message=assistant_message,
                    model_response=model_response,
                    response_format=response_format,
                    tools=_tool_dicts,
                    tool_choice=tool_choice or self._tool_choice,
                    run_response=run_response,
                    compress_tool_results=_compress_tool_results,
                )
                # Accumulate metrics for non-streamed response within stream
                if run_response is not None and model_response.response_usage is not None:
                    from egpt.metrics import accumulate_model_metrics

                    accumulate_model_metrics(model_response, self, self.model_type, run_response.metrics)
                if self.cache_response:
                    streaming_responses.append(model_response)
                yield model_response

            # Add assistant message to messages
            messages.append(assistant_message)
            assistant_message.log(metrics=True)

            # Emit LLM request completed event with metrics
            llm_metrics = assistant_message.metrics
            yield ModelResponse(
                event=ModelResponseEvent.model_request_completed.value,
                input_tokens=llm_metrics.input_tokens if llm_metrics else None,
                output_tokens=llm_metrics.output_tokens if llm_metrics else None,
                total_tokens=llm_metrics.total_tokens if llm_metrics else None,
                time_to_first_token=llm_metrics.time_to_first_token if llm_metrics else None,
                reasoning_tokens=llm_metrics.reasoning_tokens if llm_metrics else None,
                cache_read_tokens=llm_metrics.cache_read_tokens if llm_metrics else None,
                cache_write_tokens=llm_metrics.cache_write_tokens if llm_metrics else None,
            )

            # Handle tool calls if present
            if assistant_message.tool_calls is not None:
                # Prepare function calls
                function_calls_to_run: List[FunctionCall] = self.get_function_calls_to_run(
                    assistant_message=assistant_message, messages=messages, functions=_functions
                )
                function_call_results: List[Message] = []

                # Execute function calls
                async for function_call_response in self.arun_function_calls(
                    function_calls=function_calls_to_run,
                    function_call_results=function_call_results,
                    current_function_call_count=function_call_count,
                    function_call_limit=tool_call_limit,
                ):
                    if self.cache_response and isinstance(function_call_response, ModelResponse):
                        streaming_responses.append(function_call_response)
                    yield function_call_response

                # Add a function call for each successful execution
                function_call_count += len(function_call_results)

                # Format and add results to messages
                if stream_data and stream_data.extra is not None:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                        **stream_data.extra,
                    )
                elif model_response and model_response.extra is not None:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                        **model_response.extra or {},
                    )
                else:
                    self.format_function_call_results(
                        messages=messages,
                        function_call_results=function_call_results,
                        compress_tool_results=_compress_tool_results,
                    )

                # Handle function call media
                if any(msg.images or msg.videos or msg.audio or msg.files for msg in function_call_results):
                    self._handle_function_call_media(
                        messages=messages,
                        function_call_results=function_call_results,
                        send_media_to_model=send_media_to_model,
                    )

                for function_call_result in function_call_results:
                    function_call_result.log(metrics=True, use_compressed_content=_compress_tool_results)

                # Check if we should stop after tool calls
                if any(m.stop_after_tool_call for m in function_call_results):
                    break

                # Per-turn checkpoint hook: post-gather barrier. Tool results have been
                # appended to messages; fire the hook before deciding whether to loop or break.
                # Failure to checkpoint must not kill a working run — log and continue.
                if after_tool_results is not None:
                    try:
                        await after_tool_results(model_response)
                    except Exception as e:
                        log_error(f"after_tool_results callback failed: {e}")

                # If we have any tool calls that require confirmation, break the loop
                if any(fc.function.requires_confirmation for fc in function_calls_to_run):
                    break

                # If we have any tool calls that require external execution, break the loop
                if any(fc.function.external_execution for fc in function_calls_to_run):
                    break

                # If we have any tool calls that require user input, break the loop
                if any(fc.function.requires_user_input for fc in function_calls_to_run):
                    break

                # Check if run_response has requirements (e.g., from member agent HITL)
                # This handles cases where a tool (like delegate_task_to_member) propagates
                # HITL requirements from a member agent to the team's run_response
                if run_response is not None and run_response.requirements:
                    if any(not req.is_resolved() for req in run_response.requirements):
                        break

                # Continue loop to get next response
                continue

            # No tool calls or finished processing them
            break

        log_debug(f"{self.get_provider()} Async Response Stream End", center=True, symbol="-")

        # Save streaming responses to cache if enabled
        if self.cache_response and cache_key and streaming_responses:
            self._save_streaming_responses_to_cache(cache_key, streaming_responses)

    def _populate_assistant_message_from_stream_data(
        self, assistant_message: Message, stream_data: MessageData
    ) -> None:
        """
        Populate an assistant message with the stream data.
        """
        if stream_data.response_role is not None:
            assistant_message.role = stream_data.response_role
        if stream_data.response_metrics is not None:
            assistant_message.metrics = stream_data.response_metrics
        # Same accounting as the non-streaming path -- see `_finalize_call_accounting`. Streaming is
        # the default on the HTTP routers, so omitting it here meant most real traffic got neither a
        # cost nor a settled reservation. Outside the `response_metrics` guard for the same reason as
        # the non-streaming path: a stream that carried no usage chunk must still settle its turn.
        if assistant_message.metrics is not None:
            self._finalize_call_accounting(assistant_message.metrics)
        if stream_data.response_content:
            assistant_message.content = stream_data.response_content
        if stream_data.response_reasoning_content:
            assistant_message.reasoning_content = stream_data.response_reasoning_content
        if stream_data.response_redacted_reasoning_content:
            assistant_message.redacted_reasoning_content = stream_data.response_redacted_reasoning_content
        if stream_data.response_provider_data:
            assistant_message.provider_data = stream_data.response_provider_data
        if stream_data.response_citations:
            assistant_message.citations = stream_data.response_citations
        if stream_data.response_audio:
            assistant_message.audio_output = stream_data.response_audio
        if stream_data.response_image:
            assistant_message.image_output = stream_data.response_image
        if stream_data.response_video:
            assistant_message.video_output = stream_data.response_video
        if stream_data.response_file:
            assistant_message.file_output = stream_data.response_file
        if stream_data.response_tool_calls and len(stream_data.response_tool_calls) > 0:
            parsed_tool_calls = self.parse_tool_calls(stream_data.response_tool_calls)
            # Ensure every tool call has an id — some providers (e.g. Ollama) omit it
            for tc in parsed_tool_calls:
                if not tc.get("id"):
                    tc["id"] = str(uuid4())
            assistant_message.tool_calls = parsed_tool_calls

    def _populate_stream_data(
        self, stream_data: MessageData, model_response_delta: ModelResponse
    ) -> Iterator[ModelResponse]:
        """Update the stream data and assistant message with the model response."""

        should_yield = False
        if model_response_delta.role is not None:
            stream_data.response_role = model_response_delta.role  # type: ignore

        if model_response_delta.response_usage is not None:
            if stream_data.response_metrics is None:
                # Initialize if not already initialized (shouldn't happen, but safety check)
                stream_data.response_metrics = MessageMetrics()
                stream_data.response_metrics.start_timer()
            # Update Metrics with usage data from response
            usage = model_response_delta.response_usage
            # Use in-place addition to preserve timer automatically
            stream_data.response_metrics += usage

        # Update stream_data content
        if model_response_delta.content is not None:
            stream_data.response_content += model_response_delta.content
            # Set time_to_first_token on first content chunk if not already set
            if stream_data.response_metrics is not None and stream_data.response_metrics.time_to_first_token is None:
                stream_data.response_metrics.set_time_to_first_token()
            should_yield = True

        if model_response_delta.reasoning_content is not None:
            stream_data.response_reasoning_content += model_response_delta.reasoning_content
            should_yield = True

        if model_response_delta.redacted_reasoning_content is not None:
            stream_data.response_redacted_reasoning_content += model_response_delta.redacted_reasoning_content
            should_yield = True

        if model_response_delta.citations is not None:
            stream_data.response_citations = model_response_delta.citations
            should_yield = True

        if model_response_delta.provider_data:
            if stream_data.response_provider_data is None:
                stream_data.response_provider_data = {}
            # List-aware merge: extend lists (e.g. server_tool_blocks), replace scalars
            for key, value in model_response_delta.provider_data.items():
                existing = stream_data.response_provider_data.get(key)
                if isinstance(existing, list) and isinstance(value, list):
                    existing.extend(value)
                else:
                    stream_data.response_provider_data[key] = value

        # Update stream_data tool calls
        if model_response_delta.tool_calls is not None:
            if stream_data.response_tool_calls is None:
                stream_data.response_tool_calls = []
            stream_data.response_tool_calls.extend(model_response_delta.tool_calls)
            should_yield = True

        if model_response_delta.audio is not None and isinstance(model_response_delta.audio, Audio):
            if stream_data.response_audio is None:
                stream_data.response_audio = Audio(id=str(uuid4()), content="", transcript="")

            from typing import cast

            audio_response = cast(Audio, model_response_delta.audio)

            # Update the stream data with audio information
            if audio_response.id is not None:
                stream_data.response_audio.id = audio_response.id  # type: ignore
            if audio_response.content is not None:
                stream_data.response_audio.content += audio_response.content  # type: ignore
            if audio_response.transcript is not None:
                stream_data.response_audio.transcript += audio_response.transcript  # type: ignore
            if audio_response.expires_at is not None:
                stream_data.response_audio.expires_at = audio_response.expires_at
            if audio_response.mime_type is not None:
                stream_data.response_audio.mime_type = audio_response.mime_type
            stream_data.response_audio.sample_rate = audio_response.sample_rate
            stream_data.response_audio.channels = audio_response.channels

            should_yield = True

        if model_response_delta.images:
            if stream_data.response_image is None:
                stream_data.response_image = model_response_delta.images[-1]
            should_yield = True

        if model_response_delta.videos:
            if stream_data.response_video is None:
                stream_data.response_video = model_response_delta.videos[-1]
            should_yield = True

        if model_response_delta.extra is not None:
            if stream_data.extra is None:
                stream_data.extra = {}
            for key in model_response_delta.extra:
                if isinstance(model_response_delta.extra[key], list):
                    if not stream_data.extra.get(key):
                        stream_data.extra[key] = []
                    stream_data.extra[key].extend(model_response_delta.extra[key])
                else:
                    stream_data.extra[key] = model_response_delta.extra[key]

        if should_yield:
            yield model_response_delta

    def parse_tool_calls(self, tool_calls_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parse the tool calls from the model provider into a list of tool calls.
        """
        return tool_calls_data

    def get_function_call_to_run_from_tool_execution(
        self,
        tool_execution: ToolExecution,
        functions: Optional[Dict[str, Function]] = None,
    ) -> FunctionCall:
        function_call = get_function_call_for_tool_execution(
            tool_execution=tool_execution,
            functions=functions,
        )
        if function_call is None:
            raise ValueError("Function call not found")
        return function_call

    def get_function_calls_to_run(
        self,
        assistant_message: Message,
        messages: List[Message],
        functions: Optional[Dict[str, Function]] = None,
    ) -> List[FunctionCall]:
        """
        Prepare function calls for the assistant message.
        """
        function_calls_to_run: List[FunctionCall] = []
        if assistant_message.tool_calls is not None:
            for tool_call in assistant_message.tool_calls:
                _tool_call_id = tool_call.get("id")
                _tool_call_name = tool_call.get("function", {}).get("name")
                _function_call = get_function_call_for_tool_call(tool_call, functions)
                if _function_call is None:
                    messages.append(
                        Message(
                            role=self.tool_message_role,
                            tool_call_id=_tool_call_id,
                            tool_name=_tool_call_name,
                            content="Error: The requested tool does not exist or is not available.",
                        )
                    )
                    continue
                if _function_call.error is not None:
                    messages.append(
                        Message(
                            role=self.tool_message_role,
                            tool_call_id=_tool_call_id,
                            tool_name=_tool_call_name,
                            content=_function_call.error,
                        )
                    )
                    continue
                function_calls_to_run.append(_function_call)
        return function_calls_to_run

    def create_function_call_result(
        self,
        function_call: FunctionCall,
        success: bool,
        output: Optional[Union[List[Any], str]] = None,
        timer: Optional[Timer] = None,
        function_execution_result: Optional[FunctionExecutionResult] = None,
    ) -> Message:
        """Create a function call result message."""
        kwargs: Dict[str, Any] = {}
        # Tool messages don't get metrics - only assistant messages do

        # Include media artifacts from function execution result in the tool message
        images = None
        videos = None
        audios = None
        files = None

        if success and function_execution_result:
            # With unified classes, no conversion needed - use directly
            images = function_execution_result.images
            videos = function_execution_result.videos
            audios = function_execution_result.audios
            files = function_execution_result.files

        return Message(
            role=self.tool_message_role,
            content=output if success else function_call.error,
            tool_call_id=function_call.call_id,
            tool_name=function_call.function.name,
            tool_args=function_call.arguments,
            tool_call_error=not success,
            stop_after_tool_call=function_call.function.stop_after_tool_call,
            images=images,
            videos=videos,
            audio=audios,
            files=files,
            **kwargs,  # type: ignore
        )

    def create_tool_call_limit_error_result(self, function_call: FunctionCall) -> Message:
        return Message(
            role=self.tool_message_role,
            content=f"Tool call limit reached. Tool call {function_call.function.name} not executed. Don't try to execute it again.",
            tool_call_id=function_call.call_id,
            tool_name=function_call.function.name,
            tool_args=function_call.arguments,
            tool_call_error=True,
        )

    def run_function_call(
        self,
        function_call: FunctionCall,
        function_call_results: List[Message],
        additional_input: Optional[List[Message]] = None,
    ) -> Iterator[Union[ModelResponse, RunOutputEvent, TeamRunOutputEvent]]:
        # Start function call
        function_call_timer = Timer()
        function_call_timer.start()
        # Yield a tool_call_started event
        yield ModelResponse(
            content=function_call.get_call_str(),
            tool_executions=[
                ToolExecution(
                    tool_call_id=function_call.call_id,
                    tool_name=function_call.function.name,
                    tool_args=function_call.arguments,
                )
            ],
            event=ModelResponseEvent.tool_call_started.value,
        )

        # Run function calls sequentially
        function_execution_result: FunctionExecutionResult = FunctionExecutionResult(status="failure")
        stop_after_tool_call_from_exception = False
        try:
            function_execution_result = function_call.execute()
        except AgentRunException as a_exc:
            # Update additional messages from function call
            _handle_agent_exception(a_exc, additional_input)
            # If stop_execution is True, mark that we should stop after this tool call
            if a_exc.stop_execution:
                stop_after_tool_call_from_exception = True
            # Set function call success to False if an exception occurred
        except RunCancelledException:
            raise
        except Exception as e:
            log_error(f"Error executing function {function_call.function.name}: {str(e)}")
            raise e

        function_call_success = function_execution_result.status == "success"

        # Stop function call timer
        function_call_timer.stop()

        # Process function call output
        function_call_output: str = ""

        if isinstance(function_execution_result.result, (GeneratorType, collections.abc.Iterator)):
            try:
                for item in function_execution_result.result:
                    # This function yields agent/team/workflow run events
                    if (
                        isinstance(item, tuple(get_args(RunOutputEvent)))
                        or isinstance(item, tuple(get_args(TeamRunOutputEvent)))
                        or isinstance(item, tuple(get_args(WorkflowRunOutputEvent)))
                    ):
                        # We only capture content events for output accumulation
                        if isinstance(item, RunContentEvent) or isinstance(item, TeamRunContentEvent):
                            if item.content is not None and isinstance(item.content, BaseModel):
                                function_call_output += item.content.model_dump_json()
                            else:
                                # Capture output
                                function_call_output += item.content or ""

                            if function_call.function.show_result and item.content is not None:
                                yield ModelResponse(content=item.content)

                        if isinstance(item, CustomEvent):
                            function_call_output += str(item)
                            item.tool_call_id = function_call.call_id

                        # For WorkflowCompletedEvent, extract content for final output
                        from egpt.run.workflow import WorkflowCompletedEvent

                        if isinstance(item, WorkflowCompletedEvent):
                            if item.content is not None:
                                if isinstance(item.content, BaseModel):
                                    function_call_output += item.content.model_dump_json()
                                else:
                                    function_call_output += str(item.content)

                        # Yield the event itself to bubble it up. The isinstance guards
                        # above narrow item at runtime, but mypy cannot see through
                        # tuple(get_args(...)).
                        yield item  # type: ignore[misc]

                    else:
                        function_call_output += str(item)
                        if function_call.function.show_result and item is not None:
                            yield ModelResponse(content=str(item))
            except RunCancelledException:
                raise
            except Exception as e:
                log_error(
                    f"Error while iterating function result generator for {function_call.function.name}: {str(e)}"
                )
                function_call.error = str(e)
                function_call_success = False

            # For generators, re-capture updated_session_state after consumption
            # since session_state modifications were made during iteration
            if function_execution_result.updated_session_state is None:
                if (
                    function_call.function._run_context is not None
                    and function_call.function._run_context.session_state is not None
                ):
                    function_execution_result.updated_session_state = function_call.function._run_context.session_state
        else:
            from egpt.tools.function import ToolResult

            if isinstance(function_execution_result.result, ToolResult):
                # Extract content and media from ToolResult
                tool_result = function_execution_result.result
                function_call_output = tool_result.content

                # Transfer media from ToolResult to FunctionExecutionResult
                if tool_result.images:
                    function_execution_result.images = tool_result.images
                if tool_result.videos:
                    function_execution_result.videos = tool_result.videos
                if tool_result.audios:
                    function_execution_result.audios = tool_result.audios
                if tool_result.files:
                    function_execution_result.files = tool_result.files
            else:
                function_call_output = str(function_execution_result.result) if function_execution_result.result else ""

            if function_call.function.show_result and function_call_output is not None:
                yield ModelResponse(content=function_call_output)

        # Create ToolCallMetrics for the tool execution
        tool_metrics = None
        if function_call_timer is not None and function_call_timer.elapsed > 0:
            from time import time

            tool_metrics = ToolCallMetrics()
            tool_metrics.timer = function_call_timer
            tool_metrics.duration = function_call_timer.elapsed
            # Calculate Unix timestamps (Timer uses perf_counter which is relative)
            current_time = time()
            tool_metrics.end_time = current_time
            tool_metrics.start_time = current_time - function_call_timer.elapsed

        # Create and yield function call result
        function_call_result = self.create_function_call_result(
            function_call,
            success=function_call_success,
            output=function_call_output,
            timer=function_call_timer,
            function_execution_result=function_execution_result,
        )
        # Override stop_after_tool_call if set by exception
        if stop_after_tool_call_from_exception:
            function_call_result.stop_after_tool_call = True
        yield ModelResponse(
            content=f"{function_call.get_call_str()} completed in {function_call_timer.elapsed:.4f}s. ",
            tool_executions=[
                ToolExecution(
                    tool_call_id=function_call_result.tool_call_id,
                    tool_name=function_call_result.tool_name,
                    tool_args=function_call_result.tool_args,
                    tool_call_error=function_call_result.tool_call_error,
                    result=str(function_call_result.content),
                    stop_after_tool_call=function_call_result.stop_after_tool_call,
                    metrics=tool_metrics,
                )
            ],
            event=ModelResponseEvent.tool_call_completed.value,
            updated_session_state=function_execution_result.updated_session_state,
            # Add media artifacts from function execution
            images=function_execution_result.images,
            videos=function_execution_result.videos,
            audios=function_execution_result.audios,
            files=function_execution_result.files,
        )

        # Add function call to function call results
        function_call_results.append(function_call_result)

    def run_function_calls(
        self,
        function_calls: List[FunctionCall],
        function_call_results: List[Message],
        additional_input: Optional[List[Message]] = None,
        current_function_call_count: int = 0,
        function_call_limit: Optional[int] = None,
    ) -> Iterator[Union[ModelResponse, RunOutputEvent, TeamRunOutputEvent]]:
        # Additional messages from function calls that will be added to the function call results
        if additional_input is None:
            additional_input = []

        for fc in function_calls:
            if function_call_limit is not None:
                current_function_call_count += 1
                # We have reached the function call limit, so we add an error result to the function call results
                if current_function_call_count > function_call_limit:
                    function_call_results.append(self.create_tool_call_limit_error_result(fc))
                    continue

            paused_tool_executions = []

            # The function requires user confirmation (HITL)
            if fc.function.requires_confirmation:
                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_confirmation=True,
                        approval_type=fc.function.approval_type,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )

            # The function requires user input (HITL)
            if fc.function.requires_user_input:
                user_input_schema = fc.function.user_input_schema
                if fc.arguments and user_input_schema:
                    for name, value in fc.arguments.items():
                        for user_input_field in user_input_schema:
                            if user_input_field.name == name:
                                user_input_field.value = value

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        approval_type=fc.function.approval_type,
                        user_input_schema=user_input_schema,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )

            # If the function is from the user control flow (HITL) tools, we handle it here
            if fc.function.name == "get_user_input" and fc.arguments and fc.arguments.get("user_input_fields"):
                user_input_schema = []
                for input_field in fc.arguments.get("user_input_fields", []):
                    field_type = input_field.get("field_type")
                    if isinstance(field_type, str):
                        type_mapping = {
                            "str": str,
                            "int": int,
                            "float": float,
                            "bool": bool,
                            "list": list,
                            "dict": dict,
                        }
                        python_type = type_mapping.get(field_type, str)
                    elif isinstance(field_type, type):
                        python_type = field_type
                    else:
                        python_type = str
                    user_input_schema.append(
                        UserInputField(
                            name=input_field.get("field_name"),
                            field_type=python_type,
                            description=input_field.get("field_description"),
                        )
                    )

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        user_input_schema=user_input_schema,
                    )
                )

            # If the function is from the user feedback tools, we handle it here
            if fc.function.name == "ask_user" and fc.arguments and fc.arguments.get("questions"):
                user_feedback_schema = []
                for q in fc.arguments.get("questions", []):
                    options = None
                    if q.get("options"):
                        options = [
                            UserFeedbackOption(label=opt.get("label", ""), description=opt.get("description"))
                            for opt in q["options"]
                        ]
                    user_feedback_schema.append(
                        UserFeedbackQuestion(
                            question=q.get("question", ""),
                            header=q.get("header"),
                            options=options,
                            multi_select=q.get("multi_select", False),
                        )
                    )

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        user_feedback_schema=user_feedback_schema,
                    )
                )

            # The function requires external execution (HITL)
            if fc.function.external_execution:
                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        external_execution_required=True,
                        approval_type=fc.function.approval_type,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )

            if paused_tool_executions:
                yield ModelResponse(
                    tool_executions=paused_tool_executions,
                    event=ModelResponseEvent.tool_call_paused.value,
                )
                # We don't execute the function calls here
                continue

            yield from self.run_function_call(
                function_call=fc, function_call_results=function_call_results, additional_input=additional_input
            )

        # Add any additional messages at the end
        if additional_input:
            function_call_results.extend(additional_input)

    async def arun_function_call(
        self,
        function_call: FunctionCall,
    ) -> Tuple[Union[bool, AgentRunException], Timer, FunctionCall, FunctionExecutionResult]:
        """Run a single function call and return its success status, timer, and the FunctionCall object."""
        from inspect import isasyncgenfunction, iscoroutine, iscoroutinefunction

        function_call_timer = Timer()
        function_call_timer.start()
        success: Union[bool, AgentRunException] = False
        result: FunctionExecutionResult = FunctionExecutionResult(status="failure")

        try:
            if (
                iscoroutinefunction(function_call.function.entrypoint)
                or isasyncgenfunction(function_call.function.entrypoint)
                or iscoroutine(function_call.function.entrypoint)
            ):
                result = await function_call.aexecute()
                success = result.status == "success"

            # If any of the hooks are async, we need to run the function call asynchronously
            elif function_call.function.tool_hooks is not None and any(
                iscoroutinefunction(f) for f in function_call.function.tool_hooks
            ):
                result = await function_call.aexecute()
                success = result.status == "success"
            else:
                result = await asyncio.to_thread(function_call.execute)
                success = result.status == "success"
        except AgentRunException as e:
            success = e
        except RunCancelledException:
            raise
        except Exception as e:
            log_error(f"Error executing function {function_call.function.name}: {str(e)}")
            success = False
            raise e

        function_call_timer.stop()
        return success, function_call_timer, function_call, result

    async def arun_function_calls(
        self,
        function_calls: List[FunctionCall],
        function_call_results: List[Message],
        additional_input: Optional[List[Message]] = None,
        current_function_call_count: int = 0,
        function_call_limit: Optional[int] = None,
        skip_pause_check: bool = False,
    ) -> AsyncIterator[Union[ModelResponse, RunOutputEvent, TeamRunOutputEvent]]:
        # Additional messages from function calls that will be added to the function call results
        if additional_input is None:
            additional_input = []

        function_calls_to_run = []
        for fc in function_calls:
            if function_call_limit is not None:
                current_function_call_count += 1
                # We have reached the function call limit, so we add an error result to the function call results
                if current_function_call_count > function_call_limit:
                    function_call_results.append(self.create_tool_call_limit_error_result(fc))
                    # Skip this function call
                    continue
            function_calls_to_run.append(fc)

        # Yield tool_call_started events for all function calls or pause them
        for fc in function_calls_to_run:
            paused_tool_executions = []
            # The function cannot be executed without user confirmation
            if fc.function.requires_confirmation and not skip_pause_check:
                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_confirmation=True,
                        approval_type=fc.function.approval_type,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )
            # If the function requires user input, we yield a message to the user
            if fc.function.requires_user_input and not skip_pause_check:
                user_input_schema = fc.function.user_input_schema
                if fc.arguments and user_input_schema:
                    for name, value in fc.arguments.items():
                        for user_input_field in user_input_schema:
                            if user_input_field.name == name:
                                user_input_field.value = value

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        approval_type=fc.function.approval_type,
                        user_input_schema=user_input_schema,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )
            # If the function is from the user control flow tools, we handle it here
            if (
                fc.function.name == "get_user_input"
                and fc.arguments
                and fc.arguments.get("user_input_fields")
                and not skip_pause_check
            ):
                fc.function.requires_user_input = True
                user_input_schema = []
                for input_field in fc.arguments.get("user_input_fields", []):
                    field_type = input_field.get("field_type")
                    if isinstance(field_type, str):
                        type_mapping = {
                            "str": str,
                            "int": int,
                            "float": float,
                            "bool": bool,
                            "list": list,
                            "dict": dict,
                        }
                        python_type = type_mapping.get(field_type, str)
                    elif isinstance(field_type, type):
                        python_type = field_type
                    else:
                        python_type = str
                    user_input_schema.append(
                        UserInputField(
                            name=input_field.get("field_name"),
                            field_type=python_type,
                            description=input_field.get("field_description"),
                        )
                    )

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        user_input_schema=user_input_schema,
                    )
                )
            # If the function is from the user feedback tools, we handle it here
            if (
                fc.function.name == "ask_user"
                and fc.arguments
                and fc.arguments.get("questions")
                and not skip_pause_check
            ):
                fc.function.requires_user_input = True
                user_feedback_schema = []
                for q in fc.arguments.get("questions", []):
                    options = None
                    if q.get("options"):
                        options = [
                            UserFeedbackOption(label=opt.get("label", ""), description=opt.get("description"))
                            for opt in q["options"]
                        ]
                    user_feedback_schema.append(
                        UserFeedbackQuestion(
                            question=q.get("question", ""),
                            header=q.get("header"),
                            options=options,
                            multi_select=q.get("multi_select", False),
                        )
                    )

                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_user_input=True,
                        user_feedback_schema=user_feedback_schema,
                    )
                )
            # If the function requires external execution, we yield a message to the user
            if fc.function.external_execution and not skip_pause_check:
                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        external_execution_required=True,
                        approval_type=fc.function.approval_type,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )

            if paused_tool_executions:
                yield ModelResponse(
                    tool_executions=paused_tool_executions,
                    event=ModelResponseEvent.tool_call_paused.value,
                )
                # We don't execute the function calls here
                continue

            yield ModelResponse(
                content=fc.get_call_str(),
                tool_executions=[
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                    )
                ],
                event=ModelResponseEvent.tool_call_started.value,
            )

        # Create and run all function calls in parallel (skip ones that need confirmation)
        if skip_pause_check:
            function_calls_to_run = function_calls_to_run
        else:
            function_calls_to_run = [
                fc
                for fc in function_calls_to_run
                if not (
                    fc.function.requires_confirmation
                    or fc.function.external_execution
                    or fc.function.requires_user_input
                )
            ]

        results = await asyncio.gather(
            *(self.arun_function_call(fc) for fc in function_calls_to_run), return_exceptions=True
        )

        # Separate async generators from other results for concurrent processing
        async_generator_results: List[Any] = []
        non_async_generator_results: List[Any] = []

        for result in results:
            if isinstance(result, BaseException):
                non_async_generator_results.append(result)
                continue

            function_call_success, function_call_timer, function_call, function_execution_result = result

            # Check if this result contains an async generator
            if isinstance(function_call.result, (AsyncGeneratorType, AsyncIterator)):
                async_generator_results.append(result)
            else:
                non_async_generator_results.append(result)

        # Process async generators with real-time event streaming using asyncio.Queue
        async_generator_outputs: Dict[int, Tuple[Any, str, Optional[BaseException]]] = {}
        event_queue: asyncio.Queue = asyncio.Queue()
        active_generators_count: int = len(async_generator_results)

        # Create background tasks for each async generator
        async def process_async_generator(result, generator_id):
            function_call_success, function_call_timer, function_call, function_execution_result = result
            function_call_output = ""

            try:
                async for item in function_call.result:
                    # This function yields agent/team/workflow run events
                    if isinstance(
                        item,
                        tuple(get_args(RunOutputEvent))
                        + tuple(get_args(TeamRunOutputEvent))
                        + tuple(get_args(WorkflowRunOutputEvent)),
                    ):
                        # We only capture content events
                        if isinstance(item, RunContentEvent) or isinstance(item, TeamRunContentEvent):
                            if item.content is not None and isinstance(item.content, BaseModel):
                                function_call_output += item.content.model_dump_json()
                            else:
                                # Capture output
                                function_call_output += item.content or ""

                            if function_call.function.show_result and item.content is not None:
                                await event_queue.put(ModelResponse(content=item.content))
                                continue

                        if isinstance(item, CustomEvent):
                            function_call_output += str(item)
                            item.tool_call_id = function_call.call_id

                            # For WorkflowCompletedEvent, extract content for final output
                            from egpt.run.workflow import WorkflowCompletedEvent

                            if isinstance(item, WorkflowCompletedEvent):
                                if item.content is not None:
                                    if isinstance(item.content, BaseModel):
                                        function_call_output += item.content.model_dump_json()
                                    else:
                                        function_call_output += str(item.content)

                        # Put the event into the queue to be yielded
                        await event_queue.put(item)

                    # Yield custom events emitted by the tool
                    else:
                        function_call_output += str(item)
                        if function_call.function.show_result and item is not None:
                            await event_queue.put(ModelResponse(content=str(item)))

                # Store the final output for this generator
                async_generator_outputs[generator_id] = (result, function_call_output, None)

            except Exception as e:
                # Store the exception
                async_generator_outputs[generator_id] = (result, "", e)

            # Signal that this generator is done
            await event_queue.put(("GENERATOR_DONE", generator_id))

        # Start all async generator tasks
        generator_tasks = []
        for i, result in enumerate(async_generator_results):
            task = asyncio.create_task(process_async_generator(result, i))
            generator_tasks.append(task)

        # Stream events from the queue as they arrive
        completed_generators_count = 0
        while completed_generators_count < active_generators_count:
            try:
                event = await event_queue.get()

                # Check if this is a completion signal
                if isinstance(event, tuple) and event[0] == "GENERATOR_DONE":
                    completed_generators_count += 1
                    continue

                # Yield the actual event
                yield event

            except Exception as e:
                log_error(f"Error processing async generator event: {str(e)}")
                break

        # Now process all results (non-async generators and completed async generators)
        for i, original_result in enumerate(results):
            # If result is an exception, skip processing it
            if isinstance(original_result, BaseException):
                # Cancellation is intentional, not an error — re-raise without logging
                if isinstance(original_result, RunCancelledException):
                    raise original_result
                log_error(f"Error during function call: {original_result}")
                raise original_result

            # Unpack result
            function_call_success, function_call_timer, function_call, function_execution_result = original_result

            # Check if this was an async generator that was already processed
            async_function_call_output = None
            if isinstance(function_call.result, (AsyncGeneratorType, collections.abc.AsyncIterator)):
                # Find the corresponding processed result
                async_gen_index = 0
                for j, result in enumerate(results[: i + 1]):
                    if not isinstance(result, BaseException):
                        _, _, fc, _ = result
                        if isinstance(fc.result, (AsyncGeneratorType, collections.abc.AsyncIterator)):
                            if j == i:  # This is our async generator
                                if async_gen_index in async_generator_outputs:
                                    _, async_function_call_output, error = async_generator_outputs[async_gen_index]
                                    if error:
                                        # Re-raise cancellation — it is not an error
                                        if isinstance(error, RunCancelledException):
                                            raise error
                                        # Handle async generator exceptions gracefully like sync generators
                                        log_error(
                                            f"Error while iterating async generator for {function_call.function.name}: {error}"
                                        )
                                        function_call.error = str(error)
                                        function_call_success = False
                                break
                            async_gen_index += 1

            updated_session_state = function_execution_result.updated_session_state

            # Handle AgentRunException
            stop_after_tool_call_from_exception = False
            if isinstance(function_call_success, AgentRunException):
                a_exc = function_call_success
                # Update additional messages from function call
                _handle_agent_exception(a_exc, additional_input)
                # If stop_execution is True, mark that we should stop after this tool call
                if a_exc.stop_execution:
                    stop_after_tool_call_from_exception = True
                # Set function call success to False if an exception occurred
                function_call_success = False

            # Process function call output
            function_call_output: str = ""

            # Check if this was an async generator that was already processed
            if async_function_call_output is not None:
                function_call_output = async_function_call_output
                # Events from async generators were already yielded in real-time above
            elif isinstance(function_call.result, (GeneratorType, collections.abc.Iterator)):
                try:
                    for item in function_call.result:
                        # This function yields agent/team/workflow run events
                        if isinstance(
                            item,
                            tuple(get_args(RunOutputEvent))
                            + tuple(get_args(TeamRunOutputEvent))
                            + tuple(get_args(WorkflowRunOutputEvent)),
                        ):
                            # We only capture content events
                            if isinstance(item, RunContentEvent) or isinstance(item, TeamRunContentEvent):
                                if item.content is not None and isinstance(item.content, BaseModel):
                                    function_call_output += item.content.model_dump_json()
                                else:
                                    # Capture output
                                    function_call_output += item.content or ""

                                if function_call.function.show_result and item.content is not None:
                                    yield ModelResponse(content=item.content)
                                    continue

                            elif isinstance(item, CustomEvent):
                                function_call_output += str(item)
                                item.tool_call_id = function_call.call_id

                            # Yield the event itself to bubble it up
                            yield item
                        else:
                            function_call_output += str(item)
                            if function_call.function.show_result and item is not None:
                                yield ModelResponse(content=str(item))
                except RunCancelledException:
                    raise
                except Exception as e:
                    log_error(
                        f"Error while iterating function result generator for {function_call.function.name}: {str(e)}"
                    )
                    function_call.error = str(e)
                    function_call_success = False

            # For generators (sync or async), re-capture updated_session_state after consumption
            # since session_state modifications were made during iteration
            if async_function_call_output is not None or isinstance(
                function_call.result,
                (GeneratorType, collections.abc.Iterator, AsyncGeneratorType, collections.abc.AsyncIterator),
            ):
                if updated_session_state is None:
                    if (
                        function_call.function._run_context is not None
                        and function_call.function._run_context.session_state is not None
                    ):
                        updated_session_state = function_call.function._run_context.session_state

            if not (
                async_function_call_output is not None
                or isinstance(
                    function_call.result,
                    (GeneratorType, collections.abc.Iterator, AsyncGeneratorType, collections.abc.AsyncIterator),
                )
            ):
                from egpt.tools.function import ToolResult

                if isinstance(function_execution_result.result, ToolResult):
                    tool_result = function_execution_result.result
                    function_call_output = tool_result.content

                    if tool_result.images:
                        function_execution_result.images = tool_result.images
                    if tool_result.videos:
                        function_execution_result.videos = tool_result.videos
                    if tool_result.audios:
                        function_execution_result.audios = tool_result.audios
                    if tool_result.files:
                        function_execution_result.files = tool_result.files
                else:
                    function_call_output = str(function_call.result)

                if function_call.function.show_result and function_call_output is not None:
                    yield ModelResponse(content=function_call_output)

            # Create ToolCallMetrics for the tool execution
            tool_metrics = None
            if function_call_timer is not None and function_call_timer.elapsed > 0:
                from time import time

                tool_metrics = ToolCallMetrics()
                tool_metrics.timer = function_call_timer
                tool_metrics.duration = function_call_timer.elapsed
                # Calculate Unix timestamps (Timer uses perf_counter which is relative)
                current_time = time()
                tool_metrics.end_time = current_time
                tool_metrics.start_time = current_time - function_call_timer.elapsed

            # Create and yield function call result
            function_call_result = self.create_function_call_result(
                function_call,
                success=function_call_success,
                output=function_call_output,
                timer=function_call_timer,
                function_execution_result=function_execution_result,
            )
            # Override stop_after_tool_call if set by exception
            if stop_after_tool_call_from_exception:
                function_call_result.stop_after_tool_call = True
            yield ModelResponse(
                content=f"{function_call.get_call_str()} completed in {function_call_timer.elapsed:.4f}s. ",
                tool_executions=[
                    ToolExecution(
                        tool_call_id=function_call_result.tool_call_id,
                        tool_name=function_call_result.tool_name,
                        tool_args=function_call_result.tool_args,
                        tool_call_error=function_call_result.tool_call_error,
                        result=str(function_call_result.content),
                        stop_after_tool_call=function_call_result.stop_after_tool_call,
                        metrics=tool_metrics,
                    )
                ],
                event=ModelResponseEvent.tool_call_completed.value,
                updated_session_state=updated_session_state,
                images=function_execution_result.images,
                videos=function_execution_result.videos,
                audios=function_execution_result.audios,
                files=function_execution_result.files,
            )

            # Add function call result to function call results
            function_call_results.append(function_call_result)

        # Add any additional messages at the end
        if additional_input:
            function_call_results.extend(additional_input)

    def _prepare_function_calls(
        self,
        assistant_message: Message,
        messages: List[Message],
        model_response: ModelResponse,
        functions: Optional[Dict[str, Function]] = None,
    ) -> List[FunctionCall]:
        """
        Prepare function calls from tool calls in the assistant message.
        """
        if model_response.content is None:
            model_response.content = ""
        if model_response.tool_calls is None:
            model_response.tool_calls = []

        function_calls_to_run: List[FunctionCall] = self.get_function_calls_to_run(
            assistant_message=assistant_message, messages=messages, functions=functions
        )
        return function_calls_to_run

    def format_function_call_results(
        self,
        messages: List[Message],
        function_call_results: List[Message],
        compress_tool_results: bool = False,
        **kwargs,
    ) -> None:
        """
        Format function call results.
        """
        if len(function_call_results) > 0:
            messages.extend(function_call_results)

    def _handle_function_call_media(
        self, messages: List[Message], function_call_results: List[Message], send_media_to_model: bool = True
    ) -> None:
        """
        Handle media artifacts from function calls by adding follow-up user messages for generated media if needed.
        """
        if not function_call_results:
            return

        # Collect all media artifacts from function calls
        all_images: List[Image] = []
        all_videos: List[Video] = []
        all_audio: List[Audio] = []
        all_files: List[File] = []

        for result_message in function_call_results:
            if result_message.images:
                all_images.extend(result_message.images)
                # Remove images from tool message to avoid errors on the LLMs
                result_message.images = None

            if result_message.videos:
                all_videos.extend(result_message.videos)
                result_message.videos = None

            if result_message.audio:
                all_audio.extend(result_message.audio)
                result_message.audio = None

            if result_message.files:
                all_files.extend(result_message.files)
                result_message.files = None

        # Only add media message if we should send media to model
        if send_media_to_model and (all_images or all_videos or all_audio or all_files):
            # If we have media artifacts, add a follow-up "user" message instead of a "tool"
            # message with the media artifacts which throws error for some models
            media_message = Message(
                role="user",
                content="Take note of the following content",
                images=all_images if all_images else None,
                videos=all_videos if all_videos else None,
                audio=all_audio if all_audio else None,
                files=all_files if all_files else None,
            )
            messages.append(media_message)

    def get_system_message_for_model(self, tools: Optional[List[Any]] = None) -> Optional[str]:
        return self.system_prompt

    def get_instructions_for_model(self, tools: Optional[List[Any]] = None) -> Optional[List[str]]:
        return self.instructions

    def __deepcopy__(self, memo):
        """Create a deep copy of the Model instance.

        Args:
            memo (dict): Dictionary of objects already copied during the current copying pass.

        Returns:
            Model: A new Model instance with deeply copied attributes.
        """
        from copy import copy, deepcopy

        # Create a new instance without calling __init__
        cls = self.__class__
        new_model = cls.__new__(cls)
        memo[id(self)] = new_model

        # Deep copy all attributes except client objects
        for k, v in self.__dict__.items():
            if k in {"response_format", "_tools", "_functions"}:
                continue
            # Skip client objects
            if k in {"client", "async_client", "http_client", "mistral_client", "model_client"}:
                setattr(new_model, k, None)
                continue
            try:
                setattr(new_model, k, deepcopy(v, memo))
            except Exception:
                try:
                    setattr(new_model, k, copy(v))
                except Exception:
                    setattr(new_model, k, v)

        return new_model

    def __setattr__(self, name: str, value: Any) -> None:
        """Block writing a connection attribute back onto ``self`` after it was already set once.

        This is the structural half of the invariant ``same_model`` relies on (see below): no
        provider's ``_get_client_params``/``get_client``/``get_async_client`` may resolve a
        credential or endpoint and write it back onto ``self`` -- that is the exact EGPT-171
        mutation bug. The check is "already present in ``__dict__``", not a post-construction
        flag, because that is true for every field the very first time the dataclass-generated
        ``__init__`` sets it (including via ``__deepcopy__``, which builds via ``cls.__new__``
        into an empty ``__dict__``, so every attribute is a first write there too) and false
        for every subsequent write, which is exactly what needs to be blocked. No in-tree
        provider `__post_init__` sets a connection attr a second time -- verified across every
        override in `egpt/integrations/models/`.

        A few call sites legitimately patch a connection attr on an already-constructed model
        (e.g. clearing a placeholder `api_key`, or restoring a pinned local model's `base_url`
        after `Agent.from_dict(..., require_endpoint=False)`) rather than reintroducing the bug
        this guards against. Those use `_set_connection_attr` instead of raw assignment.
        """
        if name in _CONNECTION_ATTRS_SET and name in self.__dict__:
            raise AttributeError(
                f"{type(self).__name__}.{name} is a connection attribute and cannot be "
                "reassigned after construction -- same_model() compares it live (EGPT-171). "
                "Resolve env/default fallbacks into a local variable instead of writing back "
                "onto self. For a legitimate one-off patch (e.g. restoring a pinned model's "
                "endpoint), use Model._set_connection_attr(self, name, value)."
            )
        object.__setattr__(self, name, value)

    def _set_connection_attr(self, name: str, value: Any) -> None:
        """Sanctioned post-construction write to a `_CONNECTION_ATTRS` field.

        For the few call sites that legitimately patch a restored/proxy model's connection --
        clearing a placeholder `api_key` (`models/utils.py::get_model_from_dict`), or restoring
        a pinned local model's `base_url`/`host` after `Agent.from_dict(...,
        require_endpoint=False)` -- rather than reintroducing the EGPT-171 self-mutation bug
        `__setattr__` guards against.
        """
        object.__setattr__(self, name, value)


# ---------------------------------------------------------------------------
# Model sameness, for copy/resolve decisions
# ---------------------------------------------------------------------------

# Scalar connection attributes that distinguish two models sharing a class and an id. Read with
# `getattr(m, name, None)` so this module names no integration: every one of these lives on a
# provider subclass (`OpenAIResponses.base_url`, `AzureOpenAI.azure_endpoint`,
# `AwsBedrock.aws_region`, `Gemini.project_id`, `Ollama.host`), and a model that declares none
# reads `None` on both sides and compares equal.
#
# The four **client handles** are deliberately absent -- `client`, `async_client`, `http_client`
# and `client_params`. A reconstruction rebuilds those fresh every time, so comparing them would
# report "different" on every hydration and re-open the pin laundering this exists to close.
#
# `session` (`aws/claude.py`) and `credentials` (`google/gemini.py`) are absent for a *different*
# reason, recorded separately because "rebuilt fresh" is not true of them -- a caller passes them
# explicitly, and when `session` is set `aws/claude.py` reads its frozen credentials and ignores
# every scalar below. This is a **knowingly-accepted gap**, not a ruled-out one: `boto3.Session`
# and `google.oauth2.Credentials` define no `__eq__`, so comparing them falls back to identity, a
# reconstruction is always a different object, and including them would make `same_model` return
# False on every hydration -- the laundering bug generalised to every provider. `id()` has the same
# defect. Two tenants distinguished *solely* by `session=Session(profile_name=...)` are therefore
# invisible here. If a discriminator is ever wanted, a derived scalar such as
# `session.profile_name` or `session.region_name` is the only form that does not re-open it.
#
# Naming is per-provider and inconsistent on purpose -- this tuple mirrors what the classes
# actually declare. `aws/bedrock.py` uses `aws_access_key_id`/`aws_secret_access_key` while
# `aws/claude.py` uses `aws_access_key`/`aws_secret_key`; both spellings are listed, because
# carrying only one leaves that class's entire IAM identity invisible to this comparison.
# `tests/unit/models/test_same_model.py` walks every `Model` subclass and fails on a
# credential/endpoint field that is neither listed here nor explicitly accounted for below, so a
# new provider cannot add a discriminator silently -- the failure mode otherwise is cross-tenant
# reuse, not a broken build.
_CONNECTION_ATTRS: Tuple[str, ...] = (
    # endpoints and routing
    "base_url",
    "base_host_url",  # langdb
    "host",  # ollama
    "endpoint",  # mistral
    "api_base",  # litellm
    "url",  # ibm watsonx
    "azure_endpoint",
    "azure_deployment",
    "resource",  # azure claude
    "account_id",  # cloudflare
    "gateway_id",  # cloudflare
    "api_version",
    # credentials and identity
    "api_key",
    "auth_token",  # anthropic
    "azure_ad_token",  # azure openai
    "parallel_api_key",  # google gemini
    "portkey_api_key",  # portkey
    "virtual_key",  # portkey
    "aws_access_key_id",  # aws bedrock
    "aws_secret_access_key",  # aws bedrock
    "aws_access_key",  # aws claude -- a different spelling, not a duplicate
    "aws_secret_key",  # aws claude
    "aws_session_token",  # aws bedrock + aws claude
    # tenancy / project scoping
    "organization",
    "project_id",
    "location",
    "region",  # vertexai claude -- distinct from aws_region
    "aws_region",
    "vertexai",  # google gemini: flips the whole auth path
)

#: Fast membership check for `Model.__setattr__`'s post-construction mutation guard.
_CONNECTION_ATTRS_SET: FrozenSet[str] = frozenset(_CONNECTION_ATTRS)

#: Object-valued connection inputs excluded from `same_model` by decision, not oversight. Named
#: explicitly so the drift test can distinguish "accepted gap" from "forgotten". See the note above
#: for why comparing each would be worse than the gap it leaves.
_CONNECTION_EXCLUSIONS: Tuple[str, ...] = (
    "client",
    "async_client",
    "http_client",
    "client_params",
    "session",  # aws claude -- no __eq__
    "async_session",  # aws bedrock -- same, the async twin
    "credentials",  # google gemini -- no __eq__
    # A callable credential source (`Optional[Any]`), checked alongside `api_key` /
    # `azure_ad_token` in `azure/openai_chat.py`. Excluded for the same reason as `session`: a
    # function object has no value equality, so comparing it would report "different" on every
    # reconstruction. Two tenants distinguished *solely* by their token provider are invisible
    # here; `azure_endpoint` and `resource` usually differ too, which is what carries them.
    "azure_ad_token_provider",
)

#: Fields whose names match the credential/endpoint vocabulary but which do **not** identify a
#: connection -- tool and feature toggles that ride on the same request. Listed so the drift test
#: can tell "considered and rejected" from "missed". Two models differing only in these talk to the
#: same endpoint under the same credentials, so treating them as different models would re-open the
#: pin laundering rather than close a gap.
_CONNECTION_NON_DISCRIMINATORS: Tuple[str, ...] = (
    "url_context",  # gemini: a grounding tool toggle
    "vertexai_search",  # gemini: a grounding tool toggle
    "vertexai_search_datastore",  # gemini: which datastore that tool searches
)


def same_model(candidate: Any, existing: Any) -> bool:
    """Whether two model references denote the same model *and the same connection*.

    Answers "did the caller choose a genuinely different model, or hand back the one already in
    use?" -- the question `Agent.deep_copy` and `Team.deep_copy` both ask when deciding whether
    an `update={"model": ...}` is a fresh user pin.

    Identity alone is not enough: every in-tree provider builds a fresh `Model` per resolve
    (`mode_a` and `mode_b` both go through `_construct`), so the model an agent is already using
    reaches a caller as an equal-but-distinct object, and `is not` reports it as a new pin.

    Class and id alone are not enough either, and that direction is worse. Two models can share
    both while pointing at different tenants' endpoints under different credentials --
    `OpenAIResponses(id="gpt-5.5", base_url=tenant_a)` versus the same id at `tenant_b`. Treating
    those as the same model keeps the *previous* tenant's object as the pin, and because a
    provider honouring pins returns `request.requested` unchanged, the copy then serves requests
    from the previous tenant's endpoint with the previous tenant's key.

    So: identical objects match; otherwise same class, same id, and every scalar connection
    attribute equal -- compared **live**. This is safe because no in-tree provider mutates these
    attributes after construction: ``_get_client_params`` (and its equivalents) resolve
    credentials and endpoints from the environment into local variables, never writing them back
    onto ``self``. So ``self.<attr>`` always equals what the caller declared, or ``None``, for the
    whole life of the object -- including after a client has been built.

    That invariant is enforced two ways: structurally, by ``Model.__setattr__``, which raises if
    a connection attribute is written a second time (a legitimate one-off patch, e.g. restoring
    a pinned model's endpoint, goes through ``Model._set_connection_attr`` instead); and by
    ``tests/unit/models/test_same_model.py::TestNoProviderMutatesConnectionAttrsAfterClientBuild``,
    which builds a client for every constructible provider and asserts the connection attributes
    it left unset are still unset afterward. A provider that reintroduces the mutation fails
    fast at the call site, not just in that test.

    An absent side never matches -- an agent with no model cannot already be using the one being
    offered.
    """
    if candidate is None or existing is None:
        return False
    if candidate is existing:
        return True
    if type(candidate) is not type(existing):
        return False
    if getattr(candidate, "id", None) != getattr(existing, "id", None):
        return False
    return all(getattr(candidate, name, None) == getattr(existing, name, None) for name in _CONNECTION_ATTRS)


def carry_over_pin(pin: Any) -> Any:
    """The pin a `deep_copy` should record, given the original's.

    Returns a *copy* rather than the original object. The pin is not inert state: `mode_a`'s
    resolver returns `request.requested` as the serving model (`providers/mode_a.py:277`), so the
    pinned object is what a run actually calls. `_get_client_params` no longer writes environment
    credentials onto the model it is given (aicoe-tracker#171 fixed the mutation that motivated
    this helper), but two things still make a shared pin object unsafe: `get_client()`/
    `get_async_client()` cache the built SDK client onto `self.client`/`self.async_client`, and
    `Model._set_connection_attr` -- the sanctioned escape hatch for the few call sites that
    legitimately repin a connection post-construction (e.g. restoring a `require_endpoint=False`
    model's `base_url`) -- writes straight onto the shared object too. Sharing one pin across
    copies would let either kind of write from one copy's request reach every sibling copy. That
    is on the hottest path in the system: `os/utils.py` deep-copies an agent per request, and
    `team/_utils.py` copies every member.

    Shallow rather than deep, and the guarantee is narrower than "the copies are independent":
    a shallow copy isolates *top-level rebinds* and shares every nested mutable. That is the right
    depth here because every path this helper defends against assigns a scalar attribute --
    `get_client()` does `self.client = ...`, `get_async_client()` does `self.async_client = ...`
    -- so the hazard this helper exists to close is isolated, and a deep copy would duplicate the
    clients and connection pools `deep_copy_field` deliberately shares by reference.

    What it does **not** isolate is a provider that mutates a container in place. Three providers
    did -- `openai/responses.py` aliased `self.reasoning` into its request params,
    `anthropic/claude.py` appended to `self.betas`, and `google/gemini.py` aliased
    `self.generation_config` into a local and wrote into it while building request params -- so
    copies sharing those containers saw each other's writes. All three were fixed in
    aicoe-tracker#190 by copying before writing, and
    `tests/unit/models/test_request_params_do_not_mutate_the_model.py` now scans the whole
    provider tree for the shape, so the class is closed rather than merely absent.

    That guard is a structural check over source, not a proof: it matches item assignment,
    augmented assignment, `del`, and mutating-method calls reached through `self.<attr>` or
    through a local bound to one in the same function, and it does not model aliasing that
    crosses a function boundary or is laundered through a call. A provider that writes into a
    container holding a credential (`default_headers["Authorization"]`, `client_params`) in some
    shape the scan cannot see would still need a deeper copy here rather than a shallow one.

    The copy also still satisfies `same_model` against the original -- class, id and the
    connection fields all survive it -- which matters because a pin that compared unequal would
    read as a fresh caller pin and re-pin the copy on its next resolve.

    Falls back to the original object if a model cannot be copied. A shared pin is the status quo
    ante and still resolves correctly; refusing to copy an exotic `Model` subclass would break
    `deep_copy` outright, which is the worse failure.
    """
    if pin is None:
        return None

    from copy import copy as _shallow_copy

    # `copy.copy` first: every in-tree `Model` is a dataclass, so this is the live path for all
    # 51 subclasses. `model_copy` is kept only for a third-party pydantic-based `Model`, and it
    # is tried *after* -- ordering it first meant a subclass whose `model_copy(self)` omits
    # `deep=` raised `TypeError`, got swallowed, and returned the shared original, re-admitting
    # this helper's own hazard in a case where `copy.copy` would have worked (#107 review).
    try:
        return _shallow_copy(pin)
    except Exception:
        pass

    try:
        model_copy = getattr(pin, "model_copy", None)
        if callable(model_copy):
            return model_copy(deep=False)
        raise TypeError(f"{type(pin).__name__} is neither copyable nor a pydantic model")
    except Exception as e:
        # Falling back to the shared object re-admits the very hazard this helper exists to
        # close (aicoe-tracker#172), so it is logged rather than swallowed: a deployment hitting
        # this is one `_get_client_params` call away from one request's credential landing in
        # another's pin. Still a fallback rather than a raise -- a shared pin resolves correctly
        # and is the status quo ante, while refusing to copy an exotic Model subclass would break
        # `deep_copy` outright. Mirrors `deep_copy_field`'s log-and-degrade in `agent/_utils.py`.
        log_warning(
            f"Could not copy the pinned model {type(pin).__name__}; the copy will share the "
            f"original's pin, which is unsafe if anything mutates it (aicoe-tracker#171): {e}"
        )
        return pin


def update_pins_a_new_model(update: Optional[Dict[str, Any]], new_model: Any, existing_model: Any) -> bool:
    """Whether an `update` dict passed to `deep_copy` states a fresh user pin.

    The whole predicate, shared by `Agent.deep_copy` and `Team.deep_copy` rather than just the
    comparison inside it. The two guard clauses are load-bearing, not boilerplate: without the
    `update.get("model") is not None` check an `update={"model": None}` would count as a fresh
    pin, because `same_model` reports no match for a `None` candidate -- which would drop a real
    pin. An edit to one copy of those guards that missed the other is exactly the agent/team
    drift that sharing only the innermost comparison left open.

    `new_model` is the *coerced* model off the constructed copy, not the raw `update["model"]`.
    `Agent.__init__` accepts the `"provider:id"` string form and `get_models` coerces it, so
    comparing the raw value would make every string-form caller fall through to "not a pin" --
    `type("openai:gpt-5.5") is not OpenAIResponses` -- leaving both directions of the bug open
    for them. Comparing the coerced model is `Model`-to-`Model` whichever form the caller wrote.
    """
    if update is None or update.get("model") is None:
        return False
    return not same_model(new_model, existing_model)
