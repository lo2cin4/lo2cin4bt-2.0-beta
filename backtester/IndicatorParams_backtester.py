

from typing import Any, Dict


class IndicatorParams:


    def __init__(self, indicator_type: str, **kwargs: Any) -> None:
        self.indicator_type = indicator_type
        self.params: Dict[str, Any] = {}
        self.trading_params: Dict[str, Any] = {}
        # NOTE: translated to English.
        for key, value in kwargs.items():
            setattr(self, key, value)

    def add_param(self, name: str, value: Any, param_type: str = "numeric") -> None:

        self.params[name] = {"value": value, "type": param_type}

    def set_trading_params(self, **kwargs: Any) -> None:

        self.trading_params.update(kwargs)

    def get_param(self, name: str, default: Any = None) -> Any:

        if name in self.params:
            return self.params[name]["value"]
        return default

    def to_dict(self) -> Dict[str, Any]:

        result = {
            "indicator_type": self.indicator_type,
            **{k: v["value"] for k, v in self.params.items()},
            **self.trading_params,
        }
        return result

    def get_param_hash(self) -> str:

        import hashlib
        import json

        # NOTE: translated to English.
        param_dict = {
            "indicator_type": self.indicator_type,
            **{k: v["value"] for k, v in self.params.items()},
            **self.trading_params,
        }

        # NOTE: translated to English.
        param_str = json.dumps(param_dict, sort_keys=True)
        return hashlib.md5(param_str.encode(), usedforsecurity=False).hexdigest()[:16]
