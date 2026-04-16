"""
Valuation module.
Financial model, scenarios, DCF, and multiples-based valuation.
"""
from .model import FinancialModel, ModelAssumptions, build_model_from_dossier
from .scenarios import ScenarioEngine, Scenario