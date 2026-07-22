from fastapi import APIRouter, Depends

from .. import pricing
from .. import pricing_config as pricing_config_module
from ..audit import log_audit
from ..auth import require_user
from ..models import PricingConfig
from ..pricing import ProviderStack

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


@router.get("/matrix")
def get_pricing_matrix(_: dict = Depends(require_user)) -> dict:
    rates = pricing_config_module.get_effective_rates()
    return {"stt": rates["stt"], "llm": rates["llm"], "tts": rates["tts"], "telephony": rates["telephony"]}


@router.get("/tiers")
def get_pricing_tiers(_: dict = Depends(require_user)) -> list[dict]:
    rates = pricing_config_module.get_effective_rates()
    return pricing.named_tiers(rates["stt"], rates["llm"], rates["tts"], rates["telephony"])


@router.get("/budget-route")
def get_budget_route(max_inr_per_min: float, _: dict = Depends(require_user)) -> dict:
    rates = pricing_config_module.get_effective_rates()
    stack: ProviderStack = pricing.select_stack_for_budget(
        max_inr_per_min, rates["stt"], rates["llm"], rates["tts"], rates["telephony"]
    )
    return {
        "stt": stack.stt,
        "llm": stack.llm,
        "tts": stack.tts,
        "telephony": stack.telephony,
        "estimated_cost_per_min": pricing.estimate_cost_per_min(
            stack, rates["stt"], rates["llm"], rates["tts"], rates["telephony"]
        ),
    }


@router.get("/admin-config", response_model=PricingConfig)
def get_admin_config(_: dict = Depends(require_user)) -> PricingConfig:
    return pricing_config_module.get_config()


@router.put("/admin-config", response_model=PricingConfig)
def update_admin_config(payload: PricingConfig, user: dict = Depends(require_user)) -> PricingConfig:
    result = pricing_config_module.update_config(payload)
    log_audit(user, "update", "pricing_config", "pricing_config")
    return result
