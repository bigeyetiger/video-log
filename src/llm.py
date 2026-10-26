"""LLM 调用封装(OpenAI 兼容端点),统一重试。"""
import time

from openai import OpenAI


def make_client(cfg: dict) -> OpenAI:
    return OpenAI(api_key=cfg["api"]["api_key"], base_url=cfg["api"]["base_url"])


def chat(client: OpenAI, model: str, messages: list, max_tokens: int, temperature: float) -> str:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:  # 网络抖动 / 限流统一退避重试
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败(已重试 3 次): {last_err}")
