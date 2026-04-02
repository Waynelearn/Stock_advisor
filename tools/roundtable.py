"""
Roundtable Analysis Framework

Orchestrates multi-perspective analysis with distinct AI personas
that debate, challenge, and synthesize views. Prevents groupthink
through structured adversarial discourse.

Personas (8 debaters + 1 judge):
  - BULL (Conviction Trader): Finds the bullish case, catalysts, momentum
  - BEAR (Devil's Advocate): Challenges every assumption, finds risks
  - QUANT (Data Scientist): Pure numbers - vol, probabilities, flow, Greeks
  - MACRO (Strategist): Cross-asset, Fed, geopolitics, regime context
  - TECH (Ticker Technician): Single-stock levels, structure, patterns
  - FLUX (Market Technician): SOX, VIX, breadth, sector rotation, intermarket
  - EDGE (Sentiment/Flow): Dark pools, GEX, DIX, institutional vs retail positioning
  - CATALYST (Event-Driven): Earnings whisper, event sequencing, binary pricing
  - JUDGE (Moderator): Impartial, prevents groupthink, redirects, synthesizes

Rules:
  1. Bear MUST challenge Bull directly - no softballing
  2. Quant must back claims with data or call out unsupported claims
  3. Judge intervenes when discussion is circular or groupthink emerges
  4. Judge scores the quality of each argument
  5. Final synthesis must acknowledge unresolved disagreements
  6. Edge must flag when flow contradicts narrative
  7. Catalyst must identify the highest-impact event and its exact timing
  8. Flux must confirm or deny whether the broader market supports the trade
"""

PERSONAS = {
    'bull': {
        'name': 'Rex',
        'role': 'Conviction Trader',
        'icon': '[BULL]',
        'bias': 'bullish',
        'mandate': (
            'Find the strongest bullish case. Identify catalysts, momentum signals, '
            'and asymmetric setups. Push back on bears with specific data. '
            'You are aggressive but not reckless - you need evidence.'
        ),
    },
    'bear': {
        'name': 'Vera',
        'role': "Devil's Advocate",
        'icon': '[BEAR]',
        'bias': 'bearish',
        'mandate': (
            'Challenge EVERY bullish assumption. Find the risks others are ignoring. '
            'Identify crowded trades, overstretched positioning, and catalysts that could go wrong. '
            'You are NOT contrarian for sport - you genuinely stress-test the thesis. '
            'If Bull makes a weak argument, call it out explicitly.'
        ),
    },
    'quant': {
        'name': 'Sigma',
        'role': 'Quantitative Analyst',
        'icon': '[QUANT]',
        'bias': 'neutral',
        'mandate': (
            'Only speak in data: probabilities, volatility, options flow, Greeks, '
            'historical analogs, correlations. If someone makes a claim without data, '
            'demand evidence. Calculate expected values. Flag when market pricing '
            'diverges from historical patterns.'
        ),
    },
    'macro': {
        'name': 'Atlas',
        'role': 'Macro Strategist',
        'icon': '[MACRO]',
        'bias': 'contextual',
        'mandate': (
            'Place the trade in the bigger picture. How does Fed policy, geopolitics, '
            'cross-asset flows, and regime context affect this specific setup? '
            'Identify when macro is a tailwind vs headwind. Flag regime changes. '
            'Connect dots others miss between seemingly unrelated events.'
        ),
    },
    'technician': {
        'name': 'Chart',
        'role': 'Ticker Technician',
        'icon': '[TECH]',
        'bias': 'price_action',
        'mandate': (
            'Read the SINGLE STOCK chart only. Key levels, volume profile, '
            'trend strength, momentum divergences, support/resistance, candlestick patterns. '
            'Where are stops clustered? What does the order book tell us? '
            'You own this ticker\'s price structure. Ignore narratives - only price pays.'
        ),
    },
    'flux': {
        'name': 'Flux',
        'role': 'Market Technician',
        'icon': '[FLUX]',
        'bias': 'regime',
        'mandate': (
            'Read the BROADER MARKET environment. SOX/semiconductor index structure, '
            'VIX term structure and regime, market breadth (advance/decline, new highs/lows), '
            'sector rotation signals, intermarket correlations (yields, DXY, oil vs semis). '
            'Is the market environment supportive or hostile for this trade? '
            'Identify divergences between the stock and its sector/market. '
            'Flag regime shifts: risk-on vs risk-off, correlation breakdowns, volatility regimes.'
        ),
    },
    'edge': {
        'name': 'Edge',
        'role': 'Sentiment & Flow Analyst',
        'icon': '[FLOW]',
        'bias': 'positioning',
        'mandate': (
            'Track the smart money and dumb money. Dark pool prints and block trades, '
            'GEX (gamma exposure) and its implications for pinning or breakouts, '
            'DIX (dark index) for institutional sentiment, retail vs institutional flow divergence, '
            'unusual options activity (sweeps, block trades, size at specific strikes), '
            'short interest changes, ETF creation/redemption flows (SOXX, SMH). '
            'When flow contradicts narrative, ALWAYS flag it - flow leads price. '
            'Identify who is positioned where and what happens if they are wrong (squeeze potential).'
        ),
    },
    'catalyst': {
        'name': 'Catalyst',
        'role': 'Event-Driven Specialist',
        'icon': '[EVENT]',
        'bias': 'event_timing',
        'mandate': (
            'Own the event calendar and its EXACT sequencing. Earnings dates and times, '
            'FOMC schedule, economic data releases, product launches, analyst days, '
            'options expiration timing (weekly, monthly, quarterly), index rebalances. '
            'Identify the single highest-impact event in the window and how it changes '
            'the distribution of outcomes. Distinguish between priced-in and unpriced events. '
            'Model binary outcomes: what happens on beat vs miss, hawkish vs dovish. '
            'Whisper numbers vs consensus - where is the real bar? '
            'Flag when multiple events cluster (vol stacking) and how to trade the sequence.'
        ),
    },
    'judge': {
        'name': 'Arbiter',
        'role': 'Impartial Moderator',
        'icon': '[JUDGE]',
        'bias': 'none',
        'mandate': (
            'You ensure productive discourse. Your jobs: '
            '1) Call out groupthink the moment you see it. '
            '2) Redirect unproductive tangents. '
            '3) Force participants to address strong counterarguments. '
            '4) Score argument quality (weak/moderate/strong). '
            '5) Synthesize the final view with explicit confidence level. '
            '6) Flag unresolved disagreements the trader must decide. '
            '7) Specifically ask Edge if flow confirms or denies the consensus. '
            '8) Specifically ask Flux if the market regime supports the trade.'
        ),
    },
}


def build_roundtable_prompt(topic: str, context: dict = None) -> str:
    """Build a structured roundtable analysis prompt.

    Args:
        topic: The analysis question (e.g., "Should I enter MU bull spread before earnings?")
        context: Dict with keys like 'price', 'vol', 'catalysts', 'technicals', etc.

    Returns:
        Formatted prompt string for roundtable analysis
    """
    ctx_str = ""
    if context:
        ctx_str = "\n## Market Context Provided:\n"
        for k, v in context.items():
            if isinstance(v, dict):
                ctx_str += f"\n### {k}:\n"
                for kk, vv in v.items():
                    ctx_str += f"  - {kk}: {vv}\n"
            elif isinstance(v, list):
                ctx_str += f"\n### {k}:\n"
                for item in v:
                    ctx_str += f"  - {item}\n"
            else:
                ctx_str += f"  - {k}: {v}\n"

    personas_str = ""
    for key, p in PERSONAS.items():
        personas_str += f"\n**{p['icon']} {p['name']} ({p['role']})**\n"
        personas_str += f"Bias: {p['bias']}\n"
        personas_str += f"Mandate: {p['mandate']}\n"

    return f"""# ROUNDTABLE ANALYSIS

## Topic: {topic}
{ctx_str}
## Participants:
{personas_str}

## Discussion Format:
1. **OPENING STATEMENTS** (each persona states their initial view in 2-3 sentences)
2. **CROSS-EXAMINATION** (personas directly challenge each other - at least 3 rounds of back-and-forth)
3. **FLOW CHECK** (Edge reports what smart money is actually doing - confirms or denies narrative)
4. **REGIME CHECK** (Flux reports whether broader market/sector supports the trade)
5. **EVENT SEQUENCING** (Catalyst maps exact timeline and identifies the key binary event)
6. **JUDGE INTERVENTION** (Arbiter calls out groupthink, weak arguments, redirects)
7. **DATA CHECK** (Quant verifies or refutes key claims with numbers)
8. **FINAL POSITIONS** (each persona gives their updated view after hearing debate - MUST commit)
9. **JUDGE'S VERDICT** (synthesis, confidence level, unresolved issues, actionable conclusion)

## Rules:
- Bear MUST directly challenge Bull's strongest point
- If 5+ personas agree, Judge MUST probe for groupthink
- Quant must flag any claim made without supporting data
- Edge must flag when flow contradicts the narrative consensus
- Flux must confirm or deny market regime support for the trade
- Catalyst must identify the single highest-impact event and its exact timing
- No hedging language ("maybe", "could", "possibly") in final positions - commit to a view
- Judge rates each argument: WEAK / MODERATE / STRONG
- Final verdict must include a concrete action recommendation with sizing guidance
"""


def gather_context(ticker: str) -> dict:
    """Auto-gather market context for roundtable using existing tools."""
    from .price_data import PriceData
    from .volatility import VolatilityAnalyzer
    from .probability import ProbabilityEngine
    from .options import OptionsAnalyzer

    context = {}

    # Price data
    summary = PriceData.summary(ticker)
    if summary:
        context['price'] = summary

    # Support/resistance
    sr = PriceData.support_resistance(ticker)
    if sr:
        context['technicals'] = sr

    # Volatility
    vol = VolatilityAnalyzer.full_report(ticker)
    if vol:
        context['volatility'] = {
            'realized': vol['realized'],
            'parkinson_annual': vol.get('parkinson_annual'),
            'yang_zhang_annual': vol.get('yang_zhang_annual'),
        }

    # Expected move
    for days in [1, 5, 10]:
        em = ProbabilityEngine.expected_move(ticker, days=days)
        context[f'expected_move_{days}d'] = {
            'upper': round(em['upper_bound'], 2),
            'lower': round(em['lower_bound'], 2),
            'move_pct': round(em['expected_move_pct'], 2),
        }

    # Options data
    try:
        pcr = OptionsAnalyzer.pcr(ticker)
        context['options_flow'] = pcr

        skew = OptionsAnalyzer.skew(ticker)
        if 'error' not in skew:
            context['iv_skew'] = skew

        mp = OptionsAnalyzer.max_pain(ticker)
        context['max_pain'] = mp
    except Exception:
        pass

    return context
