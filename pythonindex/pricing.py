"""模型 Token 與 Web Search 費用計算。"""

from config import MODEL_PRICING, WEB_SEARCH_PRICE_PER_RUN

def get_model_pricing(model):
    return MODEL_PRICING.get(model, {"input": 0.0, "cached_input": 0.0, "output": 0.0})


def calculate_model_cost(model, input_tokens, output_tokens, cached_input_tokens=0):
    """只計算模型 token 成本，不包含 Web Search。"""
    price = get_model_pricing(model)
    normal_input = max(0, int(input_tokens or 0) - int(cached_input_tokens or 0))
    cached = max(0, int(cached_input_tokens or 0))
    output = max(0, int(output_tokens or 0))
    return (
        normal_input * price["input"]
        + cached * price["cached_input"]
        + output * price["output"]
    ) / 1_000_000.0


def calculate_web_search_cost(web_search_calls=0):
    return max(0, int(web_search_calls or 0)) * WEB_SEARCH_PRICE_PER_RUN
