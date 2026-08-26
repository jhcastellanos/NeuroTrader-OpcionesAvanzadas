"""
Modelos Pydantic compartidos entre proveedores, motor de analisis y API.
Fase 1: solo los modelos necesarios para quote/bars/estado de proveedor y
modo demo. Los modelos de opciones, estrategias y posiciones se agregan
en las fases 2 y 3 (ver docs/roadmap.md).
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DataSourceStatus(str, Enum):
    LIVE = "live"
    DELAYED = "delayed"
    CACHED = "cached"
    DEMO = "demo"


class MarketSessionStatus(str, Enum):
    PRE_MARKET = "pre_market"
    OPEN = "open"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


class Quote(BaseModel):
    """Cotizacion actual de un ticker."""

    id: UUID = Field(default_factory=uuid4)
    ticker: str
    price: float
    change: float
    change_percent: float
    volume: int
    relative_volume: Optional[float] = None
    day_high: float
    day_low: float
    week52_high: float
    week52_low: float
    market_session: MarketSessionStatus
    data_source_status: DataSourceStatus
    updated_at_utc: datetime
    updated_at_ny: str  # ISO string ya convertido a America/New_York para UI
    is_demo: bool = False

    model_config = {"use_enum_values": True}


class Bar(BaseModel):
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarsResponse(BaseModel):
    ticker: str
    timeframe: str
    bars: list[Bar]
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str


class ProviderStatus(BaseModel):
    provider_name: str
    demo_mode: bool
    configured: bool
    last_check_utc: datetime
    message: str


class ContractType(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionContract(BaseModel):
    """
    Datos crudos de un contrato de opcion, tal como los entrega el
    proveedor de datos. NO incluye score, probabilidad de asignacion ni
    clasificacion de estrategia: eso pertenece al motor cuantitativo
    (Fase 3), que consume este modelo como insumo.
    """

    occ_symbol: str
    underlying_ticker: str
    contract_type: ContractType
    strike: float
    expiration_date: date
    dte: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None
    spread_dollars: Optional[float] = None
    spread_percent: Optional[float] = None
    data_source_status: DataSourceStatus
    updated_at_ny: str
    is_demo: bool = False

    model_config = {"use_enum_values": True}


class OptionChainResponse(BaseModel):
    ticker: str
    contracts: list[OptionContract]
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


class DividendEvent(BaseModel):
    ex_dividend_date: date
    pay_date: Optional[date] = None
    declaration_date: Optional[date] = None
    record_date: Optional[date] = None
    cash_amount: float


class SplitEvent(BaseModel):
    execution_date: date
    split_from: float
    split_to: float


class CorporateEventsResponse(BaseModel):
    """
    LIMITACION DOCUMENTADA: Polygon.io no expone un calendario de earnings
    en su API de acciones estandar. `earnings_available` sera False y
    `next_earnings_date` sera None en modo real hasta que se integre un
    proveedor adicional para earnings (ver docs/roadmap.md). El modo demo
    SI simula una fecha de earnings, siempre marcada como dato simulado.
    """

    ticker: str
    dividends: list[DividendEvent] = []
    splits: list[SplitEvent] = []
    next_earnings_date: Optional[date] = None
    earnings_available: bool
    earnings_note: str
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


# ═══════════════════════════════════════════════════════════════════════
# FASE 3 — Motor cuantitativo
# ═══════════════════════════════════════════════════════════════════════


class UnderlyingAnalysisRequest(BaseModel):
    ticker: str


class RiskProfile(str, Enum):
    CONSERVADOR = "conservador"
    EQUILIBRADO = "equilibrado"
    AGRESIVO = "agresivo"


class Horizon(str, Enum):
    INGRESO_SEMANAL = "ingreso_semanal"
    INGRESO_MENSUAL = "ingreso_mensual"
    ACUMULACION = "acumulacion"


class TrendDirection(str, Enum):
    ALCISTA = "alcista"
    BAJISTA = "bajista"
    LATERAL = "lateral"
    INDETERMINADO = "indeterminado"


class MarketRegime(str, Enum):
    ALCISTA = "alcista"
    BAJISTA = "bajista"
    LATERAL = "lateral"
    ALTA_VOLATILIDAD = "alta_volatilidad"


class StrategyType(str, Enum):
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"


class StrategyStatus(str, Enum):
    OPORTUNIDAD_VALIDA = "oportunidad_valida"
    ACEPTABLE_CON_PRECAUCION = "aceptable_con_precaucion"
    ESPERAR = "esperar"
    NO_APLICA = "no_aplica"


class ContractRole(str, Enum):
    CONSERVADOR = "conservador"
    EQUILIBRADO = "equilibrado"
    AGRESIVO = "agresivo"


class StrategyExplanationResponse(BaseModel):
    """
    Respuesta del panel de IA explicativa (seccion 16 del brief). El
    campo `explanation` es SIEMPRE un parafraseo en lenguaje sencillo de
    datos que el motor cuantitativo ya calculo — la IA (o la plantilla
    demo) nunca inventa cifras, cambia calculos, promete resultados ni
    reemplaza al motor.
    """

    ticker: str
    strategy: StrategyType
    explanation: str
    provider: str
    is_demo: bool
    generated_at_ny: str

    model_config = {"use_enum_values": True}


class TechnicalAnalysis(BaseModel):
    """
    Indicadores tecnicos deterministicos calculados a partir de las velas
    historicas del proveedor activo (demo o real). Ningun valor aqui es
    generado por la IA explicativa; todos son calculos matematicos puros
    (ver backend/app/engine/indicators.py), con pruebas unitarias.
    """

    ticker: str
    price: float
    sma_200: Optional[float] = None
    ema_9: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    atr_14: Optional[float] = None
    vwap_approx: Optional[float] = None
    week52_high: float
    week52_low: float
    historical_volatility_pct: Optional[float] = None
    daily_trend: TrendDirection
    weekly_trend: TrendDirection
    distance_to_key_levels_pct: dict[str, float] = Field(default_factory=dict)
    supports: list[float] = Field(default_factory=list)
    resistances: list[float] = Field(default_factory=list)
    regime: MarketRegime
    bars_used: int
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


class UnderlyingScoreBreakdown(BaseModel):
    trend_score: int
    trend_max: int = 25
    momentum_score: int
    momentum_max: int = 15
    volatility_score: int
    volatility_max: int = 20
    structure_score: int
    structure_max: int = 20
    event_risk_score: int
    event_risk_max: int = 10
    liquidity_score: int
    liquidity_max: int = 10
    total_score: int
    reasons: list[str] = Field(default_factory=list)


class UnderlyingAnalysisResponse(BaseModel):
    ticker: str
    technical: TechnicalAnalysis
    score: UnderlyingScoreBreakdown
    regime: MarketRegime
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


class VolatilityAnalysis(BaseModel):
    """
    LIMITACION DOCUMENTADA: IV Rank e IV Percentile requieren una serie
    historica de IV almacenada dia a dia, que esta app todavia no
    persiste (ver docs/roadmap.md). Por eso ambos campos quedan en None
    con una nota explicita en vez de aproximarse con un numero inventado.
    """

    historical_volatility_pct: Optional[float] = None
    atm_implied_volatility_pct: Optional[float] = None
    iv_hv_ratio: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    put_call_iv_skew_pct: Optional[float] = None
    expected_move_dollars: Optional[float] = None
    expected_move_pct: Optional[float] = None
    interpretation: str
    limitations: list[str] = Field(default_factory=list)


class ContractScoreBreakdown(BaseModel):
    liquidity_score: int
    liquidity_max: int = 20
    technical_location_score: int
    technical_location_max: int = 20
    premium_yield_score: int
    premium_yield_max: int = 15
    delta_probability_score: int
    delta_probability_max: int = 15
    volatility_score: int
    volatility_max: int = 10
    dte_score: int
    dte_max: int = 10
    event_risk_score: int
    event_risk_max: int = 10
    total_score: int
    classification: str
    reasons: list[str] = Field(default_factory=list)


class ContractEvaluation(BaseModel):
    role: ContractRole
    contract: OptionContract
    score: ContractScoreBreakdown
    metrics: dict[str, Optional[float]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    model_config = {"use_enum_values": True}


class CoveredCallRequest(BaseModel):
    ticker: str
    shares_owned: int = Field(ge=0)
    cost_basis: float = Field(gt=0)
    risk_profile: RiskProfile = RiskProfile.EQUILIBRADO
    horizon: Horizon = Horizon.INGRESO_MENSUAL
    min_yield_pct: float = Field(default=0.5, ge=0)
    willing_to_sell_shares: bool = True
    strike_must_be_above_cost_basis: bool = True
    max_contracts: int = Field(default=1, ge=1)
    min_profit_if_assigned: Optional[float] = None
    accept_earnings_before_expiration: bool = False

    model_config = {"use_enum_values": True}


class CashSecuredPutRequest(BaseModel):
    ticker: str
    capital_available: float = Field(gt=0)
    risk_profile: RiskProfile = RiskProfile.EQUILIBRADO
    horizon: Horizon = Horizon.INGRESO_MENSUAL
    min_yield_pct: float = Field(default=0.5, ge=0)
    willing_to_buy_shares: bool = True
    max_effective_price: Optional[float] = None
    max_contracts: int = Field(default=1, ge=1)
    max_portfolio_pct: Optional[float] = None
    accept_earnings_before_expiration: bool = False

    model_config = {"use_enum_values": True}


class StrategyAnalysisResponse(BaseModel):
    ticker: str
    strategy: StrategyType
    regime: MarketRegime
    status: StrategyStatus
    conviction_score: int
    summary: str
    reasons_for: list[str] = Field(default_factory=list)
    reasons_against: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    next_important_event: Optional[str] = None
    volatility: VolatilityAnalysis
    conservative: Optional[ContractEvaluation] = None
    balanced: Optional[ContractEvaluation] = None
    aggressive: Optional[ContractEvaluation] = None
    best_balance: Optional[ContractRole] = None
    alternative_action: str
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


# ═══════════════════════════════════════════════════════════════════════
# FASE 5 — Gestion (posiciones, alertas, roll center, bitacora)
# ═══════════════════════════════════════════════════════════════════════


class PositionStatus(str, Enum):
    ABIERTA = "abierta"
    BENEFICIO_OBJETIVO = "beneficio_objetivo_alcanzado"
    REVISION_NECESARIA = "revision_necesaria"
    ITM = "itm"
    PROXIMA_VENCIMIENTO = "proxima_a_vencimiento"
    PROXIMA_EARNINGS = "proxima_a_earnings"
    ASIGNADA = "asignada"
    CERRADA = "cerrada"
    ROLLED = "rolled"


class PositionCreate(BaseModel):
    ticker: str
    strategy_type: StrategyType
    occ_symbol: str
    strike: float = Field(gt=0)
    expiration_date: date
    contracts: int = Field(default=1, ge=1)
    entry_date: date
    premium_received: float = Field(ge=0)
    commissions: float = Field(default=0, ge=0)
    cost_basis_per_share: Optional[float] = Field(default=None, gt=0)
    capital_reserved: Optional[float] = Field(default=None, gt=0)
    notes: Optional[str] = None

    model_config = {"use_enum_values": True}


class PositionUpdate(BaseModel):
    """
    Campos editables despues de crear una posicion. Deliberadamente NO
    incluye `ticker`, `occ_symbol`, `strike`, `expiration_date`,
    `strategy_type` ni `entry_date`: esos definen el contrato en si —
    cambiarlos via edicion seria confuso (equivale a otra posicion
    distinta). Para eso existe cerrar y registrar una nueva.
    """

    status: Optional[PositionStatus] = None
    notes: Optional[str] = None
    contracts: Optional[int] = Field(default=None, ge=1)
    commissions: Optional[float] = Field(default=None, ge=0)
    premium_received: Optional[float] = Field(default=None, ge=0)
    cost_basis_per_share: Optional[float] = Field(default=None, gt=0)
    capital_reserved: Optional[float] = Field(default=None, gt=0)

    model_config = {"use_enum_values": True}


class PositionCloseRequest(BaseModel):
    close_premium: float = Field(ge=0)
    closed_date: Optional[date] = None
    notes: Optional[str] = None


class PositionMetrics(BaseModel):
    """
    Metricas calculadas en vivo (seccion 13 del brief). Se recalculan en
    cada consulta a partir de la cotizacion/contrato actual — nunca se
    almacenan como verdad fija, para que reflejen el mercado de hoy.
    """

    days_remaining: int
    pct_premium_captured: Optional[float] = None
    pnl_current: Optional[float] = None
    current_price: Optional[float] = None
    distance_to_strike_pct: Optional[float] = None
    current_delta: Optional[float] = None
    extrinsic_value_remaining: Optional[float] = None
    cumulative_return_pct: Optional[float] = None
    assignment_risk_note: str
    data_source_status: DataSourceStatus
    is_demo: bool = False

    model_config = {"use_enum_values": True}


class PositionResponse(BaseModel):
    id: str
    ticker: str
    strategy_type: StrategyType
    occ_symbol: str
    strike: float
    expiration_date: date
    contracts: int
    entry_date: date
    premium_received: float
    commissions: float
    cost_basis_per_share: Optional[float] = None
    capital_reserved: Optional[float] = None
    status: PositionStatus
    notes: Optional[str] = None
    closed_date: Optional[date] = None
    close_premium: Optional[float] = None
    created_at_ny: str
    updated_at_ny: str
    metrics: Optional[PositionMetrics] = None

    model_config = {"use_enum_values": True}


class PositionsSummaryResponse(BaseModel):
    """Reportes basicos (seccion 13/22): ingresos por primas y rendimiento acumulado."""

    total_positions: int
    open_positions: int
    closed_positions: int
    total_premium_collected: float
    total_commissions: float
    realized_pnl_closed: float
    monthly_premium_income: dict[str, float] = Field(default_factory=dict)


class AlertSeverity(str, Enum):
    INFO = "info"
    CAUTION = "caution"
    RISK = "risk"


class Alert(BaseModel):
    """
    Alerta interna generada dinamicamente a partir del estado actual de
    una posicion (seccion 13 del brief). NO se persiste: se recalcula en
    cada consulta a partir de datos ya calculados, para reflejar siempre
    el estado mas reciente sin arriesgar alertas obsoletas guardadas.
    """

    position_id: str
    ticker: str
    alert_type: str
    message: str
    severity: AlertSeverity

    model_config = {"use_enum_values": True}


class AlertsResponse(BaseModel):
    alerts: list[Alert]
    generated_at_ny: str


class RollAlternative(BaseModel):
    new_contract: OptionContract
    closing_cost: float
    opening_credit: float
    net_credit_debit: float
    reasons: list[str] = Field(default_factory=list)


class RollAnalysisResponse(BaseModel):
    """
    Alternativas de roll (seccion 12 del brief): SIEMPRE presenta
    opciones con su debito/credito neto y requiere decision del usuario.
    Nunca ejecuta un roll automaticamente.
    """

    position_id: str
    ticker: str
    current_strike: float
    current_expiration: date
    alternatives: list[RollAlternative]
    recommendation_note: str
    data_source_status: DataSourceStatus
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}


class JournalEntryType(str, Enum):
    NOTA = "nota"
    APERTURA = "apertura"
    CIERRE = "cierre"
    ROLL = "roll"
    ALERTA = "alerta"


class JournalEntryCreate(BaseModel):
    position_id: Optional[str] = None
    ticker: Optional[str] = None
    entry_type: JournalEntryType = JournalEntryType.NOTA
    content: str = Field(min_length=1, max_length=2000)

    model_config = {"use_enum_values": True}


class JournalEntryResponse(BaseModel):
    id: str
    position_id: Optional[str] = None
    ticker: Optional[str] = None
    entry_type: JournalEntryType
    content: str
    created_at_ny: str

    model_config = {"use_enum_values": True}


# ═══════════════════════════════════════════════════════════════════════
# FASE 6 — Calidad (backtesting, autenticacion)
# ═══════════════════════════════════════════════════════════════════════


class BacktestRequest(BaseModel):
    ticker: str
    strategy_type: StrategyType = StrategyType.COVERED_CALL
    target_delta: float = Field(default=0.25, gt=0, lt=1)
    dte_days: int = Field(default=30, ge=5, le=90)
    profit_target_pct: float = Field(default=50.0, ge=1, le=100)
    initial_capital: float = Field(default=10000.0, gt=0)
    contracts_per_cycle: int = Field(default=1, ge=1)
    reinvest: bool = False
    lookback_days: int = Field(default=400, ge=60, le=1500)

    model_config = {"use_enum_values": True}


class BacktestCycleResponse(BaseModel):
    entry_date: date
    exit_date: date
    strike: float
    entry_premium: float
    exit_premium: float
    contracts: int
    pnl: float
    assigned: bool
    closed_early: bool


class BacktestResponse(BaseModel):
    ticker: str
    strategy_type: StrategyType
    num_trades: int
    win_rate: float
    total_premium: float
    total_pnl: float
    max_drawdown_pct: float
    assignments: int
    return_on_capital_pct: float
    buy_and_hold_return_pct: float
    monthly_pnl: dict[str, float]
    best_cycle_pnl: Optional[float] = None
    worst_cycle_pnl: Optional[float] = None
    cycles: list[BacktestCycleResponse]
    limitations: list[str] = Field(default_factory=list)
    is_demo: bool = False
    data_source_status: DataSourceStatus
    updated_at_ny: str

    model_config = {"use_enum_values": True}


# -- Autenticacion (seccion 19 del brief) --------------------------------


class UserRegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserLoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    created_at_ny: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserResponse


# ═══════════════════════════════════════════════════════════════════════
# Comparador de estrategias con IA (Covered Call vs CSP simultaneo)
# ═══════════════════════════════════════════════════════════════════════


class CompareStrategiesRequest(BaseModel):
    """
    Evalua Covered Call y CSP para el mismo ticker en una sola llamada.
    Cada estrategia solo se evalua si sus datos minimos estan presentes:
    Covered Call requiere `shares_owned` + `cost_basis`; CSP requiere
    `capital_available`. Si a una le faltan datos, esa estrategia se
    omite (no se inventa), y la respuesta lo deja explicito.
    """

    ticker: str
    risk_profile: RiskProfile = RiskProfile.EQUILIBRADO
    horizon: Horizon = Horizon.INGRESO_MENSUAL
    min_yield_pct: float = Field(default=0.5, ge=0)
    accept_earnings_before_expiration: bool = False

    # Datos para evaluar Covered Call (omitir para no evaluarla)
    shares_owned: Optional[int] = Field(default=None, ge=0)
    cost_basis: Optional[float] = Field(default=None, gt=0)
    max_contracts_cc: int = Field(default=1, ge=1)
    willing_to_sell_shares: bool = True

    # Datos para evaluar CSP (omitir para no evaluarla)
    capital_available: Optional[float] = Field(default=None, gt=0)
    max_contracts_csp: int = Field(default=1, ge=1)
    willing_to_buy_shares: bool = True

    model_config = {"use_enum_values": True}


class CompareStrategiesResponse(BaseModel):
    ticker: str
    regime: MarketRegime
    covered_call: Optional[StrategyAnalysisResponse] = None
    csp: Optional[StrategyAnalysisResponse] = None
    recommended_strategy: Optional[StrategyType] = None
    recommendation_reason: str
    ai_comparison: Optional[str] = None
    ai_provider: str
    is_demo: bool = False
    updated_at_ny: str

    model_config = {"use_enum_values": True}
