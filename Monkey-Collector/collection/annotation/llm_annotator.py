"""Optional LLM-based screenshot captioning.

Provides an optional integration with LLM APIs (e.g., OpenAI GPT-4o)
to generate natural language descriptions of mobile app screenshots.
Gracefully degrades when dependencies are not installed.
"""

from loguru import logger


def generate_caption(
    screenshot_path: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> dict | None:
    """Generate a caption for a screenshot using an LLM API.

    Args:
        screenshot_path: Absolute path to the screenshot image file.
        provider: LLM provider name (currently only "openai" supported).
        model: Model identifier for the provider.

    Returns:
        Annotation dict with conversations and task_type, or None
        if captioning is not available or fails.
    """
    try:
        if provider == "openai":
            return _caption_openai(screenshot_path, model)
        else:
            logger.warning(f"Unsupported LLM provider: {provider}")
    except ImportError:
        logger.warning(
            "openai package not installed. Skipping LLM caption."
        )
    except Exception as e:
        logger.error(f"LLM captioning failed: {e}")
    return None


def _caption_openai(screenshot_path: str, model: str) -> dict | None:
    """Generate caption using OpenAI's vision API.

    Args:
        screenshot_path: Path to the screenshot image.
        model: OpenAI model identifier.

    Returns:
        Annotation dict or None on failure.
    """
    import base64

    from openai import OpenAI

    client = OpenAI()

    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this mobile app screenshot in detail. "
                            "Include the app name, visible UI elements, their "
                            "layout, and any text content."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        max_tokens=300,
    )

    caption = response.choices[0].message.content
    logger.debug(f"LLM caption generated for {screenshot_path}")

    return {
        "conversations": [
            {
                "role": "user",
                "content": "<image>\nDescribe this screenshot in detail.",
            },
            {"role": "assistant", "content": caption},
        ],
        "task_type": "caption",
    }
