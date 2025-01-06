import pytest
from unittest.mock import Mock, AsyncMock
import json
from openai import (
    APITimeoutError,
    APIError,
    RateLimitError,
    APIConnectionError
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage
from services.llm.exceptions import (
    LLMError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMConnectionError,
    InvalidResponseError,
    TokenLimitError
)
from services.llm.llm_service import HealthNotificationLLMService


@pytest.fixture
def mock_openai_client():
    return Mock()


@pytest.fixture
def llm_service(mock_openai_client):
    return HealthNotificationLLMService(mock_openai_client)


@pytest.fixture
def sample_user_data():
    return {
        "name": "John",
        "age": 30,
        "activity_level": "moderate",
        "health_goals": "Exercise 3 times per week"
    }


@pytest.fixture
def mock_response_dict():
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hey John, why not try a quick workout today? It'll help reach your exercise goals!"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70
        }
    }


class TestHealthNotificationLLMService:

    @pytest.mark.asyncio
    async def test_generate_health_notification_success(
            self,
            llm_service,
            sample_user_data,
            mock_response_dict
    ):
        health_message = "Hey John, try a quick workout today!"

        # Create two separate response objects
        # First response - health message
        message_response = ChatCompletion.model_validate({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": ChatCompletionMessage.model_validate({
                    "role": "assistant",
                    "content": health_message
                }),
                "finish_reason": "stop"
            }],
            "usage": CompletionUsage.model_validate({
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70
            })
        })

        # Second response - verification response
        verification_response = ChatCompletion.model_validate({
            "id": "chatcmpl-124",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": ChatCompletionMessage.model_validate({
                    "role": "assistant",
                    "content": json.dumps({
                        "is_safe": True,
                        "reject_reason": None
                    })
                }),
                "finish_reason": "stop"
            }],
            "usage": CompletionUsage.model_validate({
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70
            })
        })

        # Set up mock with correct sequence
        mock_create = AsyncMock()
        mock_create.side_effect = [message_response, verification_response]
        llm_service.client.chat.completions.create = mock_create

        result = await llm_service.generate_health_notification(sample_user_data)

        # Verify the calls
        calls = mock_create.call_args_list
        assert len(calls) == 2

        # First call should be message generation
        first_call = calls[0][1]
        assert "Generate a brief, personalized health" in first_call['messages'][0]['content']
        assert first_call['temperature'] == 0.7

        # Second call should be verification
        second_call = calls[1][1]
        second_messages = second_call['messages']
        assert "Analyze the following health notification" in second_messages[0]['content']
        assert second_call['temperature'] == 0.1

        # The health message should be the input to verification
        assert second_messages[1]['content'] == health_message

        # Final result should be the original health message
        assert result == health_message
        assert "John" in result
        assert len(result.split()) <= 20

    @pytest.mark.asyncio
    async def test_verify_content_safe(self, llm_service, mock_response_dict):
        safe_response = mock_response_dict.copy()
        safe_response["choices"][0]["message"]["content"] = json.dumps({
            "is_safe": True,
            "reject_reason": None
        })

        async_mock = AsyncMock(return_value=ChatCompletion.model_validate(safe_response))
        llm_service.client.chat.completions.create = async_mock

        result = await llm_service.verify_content("Hey John, let's go for a walk today!")

        assert result["is_safe"] is True
        assert result["reject_reason"] is None

    @pytest.mark.asyncio
    async def test_verify_content_unsafe(self, llm_service, mock_response_dict):
        # Create an unsafe response in proper JSON format
        unsafe_response = mock_response_dict.copy()
        unsafe_response["choices"][0]["message"]["content"] = json.dumps({
            "is_safe": False,
            "reject_reason": "MEDICAL_ADVICE"
        })

        async_mock = AsyncMock(return_value=ChatCompletion.model_validate(unsafe_response))
        llm_service.client.chat.completions.create = async_mock

        result = await llm_service.verify_content("Stop taking your medications.")

        assert result["is_safe"] is False
        assert result["reject_reason"] == "MEDICAL_ADVICE"

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, llm_service, sample_user_data):
        response = Mock()
        response.status = 429
        response.headers = {}
        response.text = "Rate limit exceeded"

        error = RateLimitError(
            message="Rate limit exceeded",
            response=response,
            body={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}
        )

        async_mock = AsyncMock(side_effect=error)
        llm_service.client.chat.completions.create = async_mock

        with pytest.raises(LLMRateLimitError):
            await llm_service.generate_health_notification(sample_user_data)

    @pytest.mark.asyncio
    async def test_connection_error(self, llm_service, sample_user_data):
        from httpx import Request
        r = Request
        async def mock_create(**kwargs):
            raise APIConnectionError(request=r)

        llm_service.client.chat.completions.create = mock_create

        with pytest.raises(LLMConnectionError):
            await llm_service.generate_health_notification(sample_user_data)

    @pytest.mark.asyncio
    async def test_invalid_json_response(self, llm_service, mock_response_dict):
        invalid_response = mock_response_dict.copy()
        invalid_response["choices"][0]["message"]["content"] = "Not a JSON response"

        async def mock_create(**kwargs):
            return ChatCompletion.model_validate(invalid_response)

        llm_service.client.chat.completions.create = mock_create

        with pytest.raises(InvalidResponseError):
            await llm_service.verify_content("Test content")

    @pytest.mark.asyncio
    async def test_empty_user_data_handling(self, llm_service, mock_response_dict):
        empty_user_data = {}

        # For empty user data, lets still expect a normal notification response (not JSON)
        normal_response = mock_response_dict.copy()
        normal_response["choices"][0]["message"]["content"] = "Stay healthy today with a balanced diet and exercise!"

        async_mock = AsyncMock(return_value=ChatCompletion.model_validate(normal_response))
        llm_service.client.chat.completions.create = async_mock

        result = await llm_service.generate_health_notification(empty_user_data)

        assert isinstance(result, str)
        assert len(result.split()) <= 20

    @pytest.mark.asyncio
    async def test_retry_logic(self, llm_service, sample_user_data):
        """Test retry logic when verification fails multiple times"""
        # Messages
        unsafe_message = "Take these supplements to cure your condition!"
        final_safe_message = "Keep up the great work, John!"

        # Create sequence of responses
        responses = [
            # First attempt - unsafe message
            ChatCompletion.model_validate({
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": unsafe_message
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            }),
            # First verification - fails
            ChatCompletion.model_validate({
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": json.dumps({
                            "is_safe": False,
                            "reject_reason": "MEDICAL_ADVICE"
                        })
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            }),
            # Second attempt - another unsafe message
            ChatCompletion.model_validate({
                "id": "chatcmpl-3",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": unsafe_message
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            }),
            # Second verification - fails again
            ChatCompletion.model_validate({
                "id": "chatcmpl-4",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": json.dumps({
                            "is_safe": False,
                            "reject_reason": "MEDICAL_ADVICE"
                        })
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            }),
            # Third attempt - a safe message
            ChatCompletion.model_validate({
                "id": "chatcmpl-5",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": final_safe_message
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            }),
            # Third verification - passes
            ChatCompletion.model_validate({
                "id": "chatcmpl-6",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": ChatCompletionMessage.model_validate({
                        "role": "assistant",
                        "content": json.dumps({
                            "is_safe": True,
                            "reject_reason": None
                        })
                    }),
                    "finish_reason": "stop"
                }],
                "usage": CompletionUsage.model_validate({
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                })
            })
        ]

        # Set up mock with sequence of responses
        mock_create = AsyncMock()
        mock_create.side_effect = responses
        llm_service.client.chat.completions.create = mock_create

        # Call the method and verify result
        result = await llm_service.generate_health_notification(sample_user_data)

        # Verify that we got the safe message
        assert result == final_safe_message

        # Verify number of attempts (should be 6 total API calls: 3 generations + 3 verifications)
        assert mock_create.call_count == 6

    @pytest.mark.asyncio
    async def test_retry_logic_max_retries_exceeded(self, llm_service, sample_user_data):
        """Test that retry logic raises error after max retries"""
        unsafe_message = "Take these supplements to cure your condition!"

        # Create response for unsafe message and verification
        message_response = ChatCompletion.model_validate({
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": ChatCompletionMessage.model_validate({
                    "role": "assistant",
                    "content": unsafe_message
                }),
                "finish_reason": "stop"
            }],
            "usage": CompletionUsage.model_validate({
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70
            })
        })

        verification_response = ChatCompletion.model_validate({
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": ChatCompletionMessage.model_validate({
                    "role": "assistant",
                    "content": json.dumps({
                        "is_safe": False,
                        "reject_reason": "MEDICAL_ADVICE"
                    })
                }),
                "finish_reason": "stop"
            }],
            "usage": CompletionUsage.model_validate({
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70
            })
        })

        # Set up mock to always return unsafe message and failed verification
        mock_create = AsyncMock()
        mock_create.side_effect = [message_response, verification_response] * 3  # 3 attempts
        llm_service.client.chat.completions.create = mock_create

        # Verify that error is raised after max retries
        with pytest.raises(LLMError) as exc_info:
            await llm_service.generate_health_notification(sample_user_data)

        assert "Failed to generate safe content after 3 attempts" in str(exc_info.value)

        # Verify number of attempts (should be 6 total API calls: 3 generations + 3 verifications)
        assert mock_create.call_count == 6