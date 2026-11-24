# Формула риска с привязкой к депозиту

_Экспортировано 14.10.2026 в 18:39:48 GMT+5 из Kodik (2.2.6)_

---

**Пользователь**

Сама по себе формула из — правильный каркас, но у неё нет одного звена: она считает риск на лот, но не привязана к доле депозита. Или я не прав? Твоя задача сейчас создать лучшую формулу, не зависимо от моего мнения

---

**Kodik**

[Рассуждение]
The user's question seems to reference a formula from some document that got cut off, likely about position sizing or risk calculation tied to deposit share. Since I'm in read-only mode, I should explore the project docs to find this formula before proposing an improved version.

---

**Kodik**

[Использование инструмента: glob]
Статус: Успешно
Цель: **/*

Glob_pattern: **/*

---

**Kodik**

[Рассуждение]
I should check the risk management docs and code, the existing tests, recent changelog entries, and related spec and strategy docs to understand the current risk manager implementation before making changes.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/04_RISK.md

Path: docs/04_RISK.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/risk_manager.py

Path: trading_bot/risk_manager.py

---

**Kodik**

[Рассуждение]
The user is questioning whether the risk formula in docs/04_RISK.md (and its alignment in 00_PLAN.md §7) properly ties per-lot risk calculation to deposit share, and wants me to design the best formula regardless of their own hypothesis. I need to check the related config spec, strategy docs, risk manager tests, and acceptance criteria to understand the current implementation before proposing improvements.

Looking at the actual formula, it does tie risk amount to a fraction of `balance_free` via `risk_amount = balance_free * risk_per_trade`, so the claim that it's disconnected from the deposit isn't entirely accurate. The real issue is more subtle: using free cash balance rather than total equity means each subsequent position's risk shrinks as capital gets locked into open trades, so the risk percentage isn't consistent across positions relative to the account's true value.

The price-to-lot conversion also needs scrutiny: multiplying stop distance by lot size only works cleanly for ruble-denominated stocks where price is per share, but futures or bonds would need point-value or percent-of-nominal adjustments, and foreign-currency-denominated shares would need FX conversion—worth checking whether the bot restricts itself to RUB equities. I should also flag that the risk-per-lot calculation is missing transaction costs like commissions and slippage on both entry and stop-exit.

The fee rate of 0.3% per side (matching Tinkoff's "Investor" tariff) is actually huge relative to typical stop distances—a 1.5% ATR-based stop plus 0.6% round-trip fees means actual risk could run 40% higher than planned, turning a theoretically 1R loss into 1.4R. This is a critical gap: the documentation only claims risk won't exceed the budget "by more than slippage + fees," essentially admitting costs aren't accounted for in the core formula, when they clearly should be folded into the budget calculation itself.

So any per-lot loss formula needs to fold in transaction costs, not just stop distance.

There's also a missing leverage/notional cap: in quiet markets with tiny ATR, the computed lot size could balloon, pushing position notional far beyond what the deposit can actually support, so a check against free funds is needed too.

Beyond that, a more complete design would also cap position size as a percentage of equity for concentration control, and possibly cap by liquidity relative to market volume — this concentration limit may be what the user actually means by "tied to the share of the deposit," rather than pure risk sizing.

Let me run a concrete example: with a tight ATR and stop distance, the risk-based formula could size a lot that consumes nearly the whole deposit, and with up to 7 such positions simultaneously, the combined exposure could exceed total equity, since the formula constrains risk per trade but not aggregate notional exposure.

This matters for gap risk too — a 100% deposit position hit by a 5% overnight gap would blow way past the intended 1% risk, which is why a separate cap on position size as a share of equity is necessary. So the user's instinct is right: the formula anchors risk to the deposit via balance_free, but leaves exposure (position size) untethered from it.

Really the honest answer is "partly wrong" — the deposit link exists through risk_amount, but several pieces are genuinely missing: using free cash instead of equity as the base, no exposure cap, costs excluded from per-lot loss, no portfolio-wide risk ceiling, no volatility regime scaling, and critically, the stop distance used for sizing has to match the real stop placement rather than a generic ATR multiple.

I'm checking docs/04_RISK.md and confirming that in ranging regime the stop sits beyond the entry candle's extremum rather than at 1.5×ATR, yet the sizing formula always uses atr * atr_stop_mult — meaning if the real stop is farther out, actual risk overshoots the budget. The fix is to size from the actual stop price/distance rather than assuming ATR, and to account for entry price realism too — limit orders have a known entry, market orders need slippage added to the best ask, so stop distance should be computed from the expected fill price under a worst-case assumption.

Now I'm also considering stop-market slippage during gaps, tying it to the configured 5bps slippage assumption, and thinking through the scaling-in rules: entries at 50%/30%/20% where risk is supposed to be computed on the aggregate position rather than per-piece, which means each add-on at a higher price raises risk unless the stop moves accordingly.

For sizing add-ons, I need the remaining budget divided by the per-lot loss at the new entry price, and the volatile regime multiplier (0.5x) just scales that risk down. I'm also working out the daily loss limit interaction — if a trade's risk could push cumulative daily losses past the 3% cap, the available risk budget should be capped at the lesser of the standard per-trade risk and whatever room remains under the daily limit.

But check 5's strict inequality still lets someone open new risk right up to the edge of the cap, and worse, it doesn't account for aggregate open-position risk — seven simultaneous 1% trades could total 7% exposure, blowing way past a 3% daily limit. So I think the real fix is tracking total portfolio heat (sum of all open risks) and requiring that adding the new trade's risk keeps that sum under the max heat threshold.

Since AGENTS.md forbids magic numbers and requires all parameters live in config.py and 07_CONFIG_SPEC.md, any new parameters I propose — like a max portfolio heat or position size cap — need to be flagged as proposals for the operator to approve, not silently added. I'm in Ask mode, so I can draft the formula and recommend doc/config updates, but I can't actually modify files myself; the operator would need to confirm and implement the change in docs/04_RISK.md and the config spec.

I'm also noting the CHANGELOG entry recording the operator's prior decision mandating the ATR-based formula from 04_RISK.md over the earlier price-based variant — this current request for the "best formula" is a new direct instruction that could supersede that, but since the risk manager is a closed zone, I need to be careful that any change only makes sizing more conservative, not riskier, and document the reasoning clearly for approval.

Switching the base from balance_free to equity could actually increase risk budget when positions are already open — e.g., with half the deposit in open positions, equity doubles relative to free cash, doubling the risk budget too. Per AGENTS.md §4.2, no automated entry point can increase risk parameters, though a human-approved code change isn't an entry point — still, I should flag this tradeoff explicitly rather than silently picking a base that increases exposure in some states, and carefully work out which base genuinely represents the "best" formula for this spec.

I want to ground this in the specific system: an intraday MOEX trading bot via T-Invest, with ATR-based stops, max 7 positions, max 2 per sector, 3% daily loss limit, 1% risk per trade, fees and slippage, scaling-in tranches, a volatility regime scaler, chaos detection halting trading, max ATR ratio caps, minimum free balance floor, and a 15% drawdown-from-start kill switch. I should check the plan, config spec, strategy docs, testing and acceptance docs, the risk manager tests, and the portfolio/config code to see how balance_free is actually defined and used elsewhere.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/00_PLAN.md

Path: docs/00_PLAN.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/03_STRATEGY.md

Path: docs/03_STRATEGY.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/07_CONFIG_SPEC.md

Path: docs/07_CONFIG_SPEC.md

---

**Kodik**

[Рассуждение]
I'm noting the key risk parameters—1% risk per trade, 0.3% fees per side, 0.05% slippage—and now I need to figure out which timeframe's ATR(14) feeds into the stop-loss calculation, since the regime classifier uses 15m ATR/ADX but the risk manager's input isn't explicit yet. I should check strategy.py to trace which ATR value gets passed through for the ranging-market stop logic.

For the ranging stop, it's placed at the extreme of the entry candle rather than by ATR multiple, which conflicts with the `atr * atr_stop_mult` sizing formula elsewhere. There's also overnight gap risk since intraday positions close by 23:30 MSK only if exchange stops cover gaps, and swing positions can be left open, plus scaling in at 50/30/20 splits—so I need to pull up the test suite, config, portfolio, and strategy files alongside the architecture, acceptance criteria, glossary, coding rules, and Tinvest API docs to nail down rounding and margin behavior.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tasks/BACKLOG.md

Path: tasks/BACKLOG.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tests/test_risk_manager.py

Path: tests/test_risk_manager.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: docs

Pattern: balance_free|equity|капитал|депозит|NAV|stop_distance|lot_size|risk_per_trade|position_size|max_lots
Path: docs

---

**Kodik**

[Рассуждение]
I should check portfolio.py for balance/equity tracking, broker/paper.py for fill logic like slippage and fees, strategy.py for ATR handling and stop placement in ranging markets, config.py for risk config fields, plus the coding rules on rounding and relevant testing and acceptance docs for M5.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/portfolio.py

Path: trading_bot/portfolio.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/broker/paper.py

Path: trading_bot/broker/paper.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/strategy.py

Pattern: atr|stop|Signal|class |def 
Path: trading_bot/strategy.py

---

**Kodik**

[Рассуждение]
I want to check the Signal class definition and where signals get created to see if a stop price is included.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/01_ARCHITECTURE.md

Path: docs/01_ARCHITECTURE.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/strategy.py

Path: trading_bot/strategy.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/02_CODING_RULES.md

Path: docs/02_CODING_RULES.md

---

**Kodik**

[Рассуждение]
I'm noting that the 15-minute ATR(14) is the absolute price value fed to the risk manager, and that Portfolio.balance tracks free cash excluding open positions. I'm flagging a subtle issue: shorting adds the notional proceeds to the free balance, which could inflate the risk budget available for subsequent trades if that formula relies on balance_free.

This confirms a real flaw — shorting a position pumps up cash and thus the risk base, unlike equity (cash plus long values minus short liabilities), which stays accurate. In actual brokerage terms, free cash should be reduced by margin requirements when shorting, but this codebase treats balance_free as raw cash, so it doesn't reflect true available funds — that's a meaningful discrepancy worth flagging.

On the long side, each successive buy eats into cash, so later trades get a smaller risk budget computed on depleted cash rather than a consistent share of total equity. With several positions open, the risk-per-trade becomes inconsistent — early trades risk closer to 1% of equity, later ones risk less, which skews the "1% risk" semantics and biases stats toward smaller later trades. There's also no deposit-share cap on position size, so a single position could consume nearly all available cash — this matches what the user might mean by "calculates risk per lot, but isn't tied to deposit share."

Actually, re-examining the formula, risk_amount = balance_free × risk_per_trade does tie to a deposit-like figure, so the user's claim isn't quite right — the real gaps are: it's anchored to the wrong base (free cash, which shrinks on longs and inflates on shorts), there's no cap on position value as a share of total deposit, trading costs sit outside the formula despite being comparable to the stop size at 0.3% commission, the stop distance used isn't the actual stop, and there's no portfolio-level risk cap tied to the remaining daily loss limit. I should work out a better formula using concrete numbers from this project, and the answer needs to be in Russian — concise but thorough.

Now I'm structuring the response: present the improved formula, explain each term, walk through a numeric example showing the difference from the current approach, propose new config parameters with defaults and justification, and list the golden/property tests that would validate it. I also need to flag that editing docs/04_RISK.md requires operator confirmation and that I can't make the edit in Ask mode, so I should offer to switch to Code mode. I'm also wary of inventing exact API details like field names for T-Invest without verification, per the project's rules against fabricating facts.

Now I'm working through the formula components domain-level, keeping margin/short inputs as verify-required TODOs. I'm defining the notation for equity, thinking through whether to use current or projected equity, and how cash, long market value, and short market value combine, especially since cash already reflects short-sale proceeds.

I'm weighing total equity against Van Tharp's core equity and reduced equity concepts for fixed-fractional position sizing — recalling that core equity subtracts capital tied up in open positions from starting equity, while reduced equity further accounts for locked-in profit from stops, and trying to decide which approach fits this system best.

This conservative base guarantees that risk sizing for new trades is computed from money that would survive a worst-case scenario where all current stops get hit, avoiding pyramiding risk onto unrealized profit. Running the numbers for a sequence of trades starting from 100k with 1% risk each, I see the sizing shrinks only gradually as open risk accumulates—by the seventh trade it's down to roughly 941 from the initial 1000, a modest but meaningful self-correcting effect.

The bigger design question is whether to use current equity or day-start equity as the base. Using min(current equity, day-start equity) means risk never increases on intraday unrealized gains but does shrink on losses—an asymmetric, conservative approach that aligns with the spirit of not letting risk grow automatically during the day. Standard fixed-fractional sizing on current equity would let risk grow with unrealized gains too, which is risky since those gains could evaporate; using equity minus open risk solves this because unrealized gains only count to the extent they're actually protected by stops.

This gives me an elegant concept: "equity-at-stops"—what the account would be worth if every open position hit its stop right now. It's cash plus the value of all positions at their stop prices, which is a naturally conservative base since it never counts unprotected profit, automatically shrinks the risk budget as open risk grows, handles shorts correctly, and can be computed directly from the journal and current stop placements without extra dependencies.

Actually, I realize this doesn't depend on mark price at all—only on stops, which makes it robust to price-feed glitches. But I need to account for gaps: if price has already slipped through the stop, I should use min(stop, mark) for longs and max(stop, mark) for shorts to reflect the actual worse liquidation value. Now I'm wondering whether the base should also be capped by the day's starting equity, especially if the bot already locked in real gains earlier in the session.

Realized profits are real money, so I don't think capping by day-start equity is necessary — equity at stops alone should serve as the base, with the daily loss limit still measured against the daily start balance separately. Rather than overengineer this, I want to identify which components actually matter versus which are just nice-to-have, starting with costs per trade as the first essential factor.

Checking typical 15-minute ATR for MOEX blue chips like SBER, the ATR-to-price ratio looks like it runs around 0.2–0.5%, meaning a 1.5×ATR stop lands around 0.3–0.75% of price — while round-trip costs of roughly 0.7% dwarf that. This means a nominal "1% risk" trade could actually realize 1.9–3.3% in losses once costs are factored in, which looks like the single biggest flaw in the whole setup.

I should present this as a hypothetical example rather than asserting specific real ATR values as fact. There's also a secondary issue worth flagging: with the take-profit at 2×ATR (~0.8% of price) and costs eating 0.7% of that, the edge becomes razor-thin — this points to a potential addition to the formula, like a costs-to-stop ratio check that flags when round-trip costs consume too much of the planned stop distance.

I'll note this as a one-line observation rather than baking it fully into the sizing formula — something like an optional `min_stop_to_cost_ratio` filter the operator could apply, since structurally unprofitable trades (where costs exceed a chunk of the stop) are a real risk at this tariff. I want to flag this without overreaching, since the user asked for the best sizing formula and this filter is adjacent but valuable.

Next I need to make sure the actual stop distance used is the real stop price (including cases where it goes beyond the entry candle's extremum in ranging markets), conservatively rounded to tick size — since this could diverge meaningfully from 1.5×ATR and directly affects correctness. I also want to account for a position notional cap as a share of equity to guard against concentration and gap risk, since with small ATR the raw risk formula could suggest buying 3x the deposit, which the funds cap would clamp but still leave one position dominating the whole book.

Thinking about what the cap actually protects against: gap/tail risk when a stop can't execute cleanly, diversification across names, and avoiding leverage on longs. A tempting principled default is setting max position size to 1/max_open_positions, which for 7 slots gives roughly 14.3% and guarantees all positions could be filled without leverage — though that might be overly conservative.

Running the numbers, if risk is 1% and the stop is 0.75% of price (1.45% with costs), a pure risk-based calculation would size positions at ~69% of equity, but a 14.3% notional cap only lets the bot actually risk about 0.21% of equity. That reveals the cap is the binding constraint almost all the time for tight intraday ATR stops, meaning the bot ends up being sized by notional rather than by risk — a significant realization that reframes how I should think about position sizing.

But then checking the actual funds-check logic, I realize it's even more extreme: the free-funds cap clamps everything to 100% of cash, so the first signal can consume all available cash and leave the remaining slots empty until it closes. Looking at the code for `_check_size_and_funds`, if total cost exceeds free balance, the verdict is an outright denial rather than a reduction — contradicting documentation that claims lots should shrink gracefully to zero instead of failing outright.

Tracing through with realistic numbers confirms this: a tight 0.4% ATR stop produces a position size that exceeds the 100k account cap, so the trade gets rejected entirely rather than resized, meaning most real-world trades with typical 15-minute ATR values would simply fail instead of scaling down as the docs promise.

The test case with price 1,000,000 and balance 100 is consistent with both reject and reduce-to-zero behaviors, so it doesn't actually distinguish the bug — but intermediate cases where reduction should apply still reveal the reject-vs-reduce discrepancy. This is a concrete, citable finding at `risk_manager.py:339-353` and the `check()` logic at lines 397-404.

For short positions specifically, the size-and-funds check compares notional plus fee against free balance, but shorts don't actually consume notional — they require margin instead, and the doc's reference to margin metrics is still unverified. So the current behavior rejects on tight stops and sizes by risk on wide stops, while the documented "reduce" behavior would go all-in — both outcomes are flawed, which is why a notional cap is needed as the real fix. I'm now working out the proper formula: lots should be the floor of a scaling factor applied to the minimum across several independent constraints — risk-based, notional-based, funds-based, liquidity-based, and daily heat limits — and I'm starting to define each input precisely, beginning with side and expected entry price.

I also need to pin down how the stop price gets rounded, since sizing must use the exact price that will actually be placed on the exchange — if the stop is rounded toward or away from entry before or after sizing, that changes the effective risk distance, so to stay conservative I should round the stop distance up to the tick size when sizing happens before the final rounding.

For the monetary side, since this bot trades only MOEX shares in rubles, I can treat the ruble value per price-unit move as simply 1 per share, rejecting or skipping instruments in other currencies rather than building in conversion logic.

Now I'm working out the per-lot loss at the stop including costs: for a long position, worst-case entry fill accounts for slippage pushing price up, and worst-case stop exit accounts for slippage pushing the exit price down further — possibly using a distinct, larger slippage assumption for stop-triggered market exits versus normal entry slippage.

Rather than inventing a default, I think it's better to calibrate the stop-slippage parameter empirically from the trade journal, which already logs actual slippage on stops. Then I'm working out the fee and loss formulas per unit and per lot for the long side, and mirroring the same structure for the short side with entry/stop prices adjusted in the opposite direction.

I'll leave out specifics like overnight short-borrow fees since those details could be wrong and swing positions are an edge case — I'll just note that any such per-position costs fold into the general "costs" term rather than detailing exact rates.

Simplifying, loss per unit becomes "cost to buy at entry minus proceeds from selling at the stop" for a long, and the mirror version for a short: entry proceeds minus the cost to cover at the stop, each adjusted by slippage and fee multipliers.

Checking against PaperBroker's actual fee computation, the fee is applied to the slipped fill price, not the raw price, so my formula matches its internals exactly — which means I can use this to build an exact property test verifying that realized loss on a stop exit never exceeds the budget. I want to trace through PaperBroker's open_position logic carefully to confirm the balance deduction formula matches.

I'm also noticing a potential double-counting of slippage: if the executor passes the already-slipped fill price plus a separate slippage cost to Portfolio.open_position, that cost gets subtracted twice. This is a separate accounting concern from the sizing formula itself, but since the executor doesn't exist yet, I can't confirm how it's actually wired — it still matters for equity consistency and the property tests though.

I want to check tests/test_portfolio.py to see how open_position is actually invoked and whether lot sizing is handled correctly, since this bears on the equity figure that feeds the risk budget formula. I'm now outlining the trade's risk budget as the minimum of the per-trade risk fraction of base and the remaining daily loss limit allowance.

Using equity-at-stops simplifies things nicely: worst-case day-end equity equals current equity-at-stops minus any new risk added, so requiring that to stay above the daily loss floor gives a clean formula — R_day = E_at_stops − (1 − d) × E_day_start. This captures "how much more I can lose today, even if every open stop fires, without breaching the daily limit." The same logic should extend to the global drawdown stop.

The global drawdown constraint follows the same structure — R_dd = E_at_stops − (1 − g) × E_start — ensuring no single trade can push the account past the 15% drawdown threshold, and folding it into the same minimum-based sizing rule is cheap and consistent. So the final risk budget becomes R = min(r × B, E_stops − (1−d)·E_day0, E_stops − (1−g)·E_start) × k_regime, rejecting the trade if R ≤ 0. I'm now sanity-checking this with a concrete example: a 100k day-start balance with d = 3% gives a 97k floor, and with seven open positions each risking roughly 1k including costs, E_stops comes out to around 93k.

That negative result means no new trades can open once three positions are live at full 1% risk, effectively turning the daily loss limit into a portfolio heat cap — which seems consistent with the intent that 3% should be a hard daily stop. But the real worry is correlation risk: MOEX stocks often move together in a sell-off even below the 0.85 correlation filter threshold, so if seven 1%-risk positions all hit stops simultaneously, realized losses could reach 7%+ before costs, or 10-15% after, far exceeding the supposed 3% daily cap.

So the current implementation only halts trading after the damage is done rather than bounding it — adding a headroom term would convert the limit from a reactive fuse into a genuine worst-case cap, which is the kind of improvement a "best formula" should deliver. The tradeoff is that this effectively restricts concurrent full-risk positions to three when r=1% and d=3%, whereas an operator might want to run seven smaller positions instead — a real policy choice I should flag as the consequence of recommending this as the default, even though the user wants the objectively best formula regardless of preference.

I'm settling on this hard worst-case bound as the better answer: capping to roughly three simultaneous full-risk positions is actually just what a 3% daily loss limit should mean, and trailing stops to breakeven naturally frees up risk budget so more positions can open later. I also need to note that stop-based bounds assume stops hold — gap risk means slippage could breach them, so I should state that caveat, and that the worst-case equity calculation at stop prices needs to include exit fees and slippage on both the liquidation value of longs and the cost to cover shorts.

Now I'm deciding how to handle an orphaned position with no stop: since its risk is unbounded, the safest rule is to treat its risk contribution as infinite, which effectively blocks new trades until a human resolves it — consistent with the existing orphan-handling policy where discrepancies get flagged and wait for human intervention. I'm also starting to weigh what the base equity reference (B) should be, comparing using equity-if-stops-hit versus the current or day-start equity as the baseline.

I'm leaning toward B = equity-at-stops since it represents the guaranteed money that would remain even if every open position hit its stop, which cleanly handles both short positions and long positions without the usual shrinking issues. I'm now double-checking how this reasoning applies specifically to long positions and their cash dynamics.

Using equity-at-stops consistently for both the r×B term and elsewhere seems cleanest since the difference from using current equity is negligible and the daily-headroom term already covers worst-case scenarios anyway — plus it avoids needing mark prices except for detecting gap-through situations. I'm now mapping which fields AccountState needs to add to support this: equity-at-stops computed from the journal, stops, and marks, alongside equity at day start.

I need to clarify whether daily_start_balance should actually track equity rather than cash, and also settle whether the 15% global drawdown stop in the docs measures from the bot's launch equity or from a running peak — the wording suggests from-launch, so I'll go with that but flag the peak-based alternative as a possible substitution. I'm leaning toward including the global drawdown term as a third component in the min formula since it's cheap, consistent with the existing pattern, and guarantees the hard 15% floor is respected even though it rarely binds in practice — the added complexity is a minor tradeoff worth making for completeness.

Now I'm working through the funds cap calculation: for long positions, the affordable lot count depends on available cash after reserving the minimum free balance of 5000, since that reserve exists to protect against fee/margin issues and should persist after any new trade rather than just being checked beforehand. For short positions, the funds constraint depends on broker margin requirements that still need verification, so in the paper-trading context I'll need to approximate with some margin model.

For a conservative approach, I'm treating short notional the same as long notional — requiring full 100% collateral with no leverage — until actual margin rules are confirmed, which avoids guessing at broker-specific behavior.

For the notional cap tied to account size, I'm computing max lots as a percentage of balance divided by lot value, and considering whether to base that percentage on current balance or equity at stop-loss — settling on balance for simplicity since the difference is minor. Now I need to figure out what the default percentage value should be.

Working through the math: with 1% risk per trade and typical stop distances plus trading costs around 1.3-3.7% of price for 15-minute ATR-based stops, I'm calculating that risk-based notional naturally lands between 27-77% of equity depending on stop width. This means a 20% or 14% cap would bind tightly in most cases, actually reducing realized risk well below the target 1%. I'm now weighing how gap risk on MOEX stocks should factor into picking a sensible default for that cap.

Total exposure across 7 positions also matters — with cash constraints, long exposure can't realistically exceed ~100%, so the worst case is bounded. A cleaner approach: set c = d/G, where d is the daily drawdown limit and G is the assumed worst-case adverse move beyond the stop.

With d = 3% and G = 15%, c comes out to 20%, which is defensible, though G itself is an assumption that should be calibrated from historical gap data rather than guessed. I could frame c as a configurable parameter with a documented default and rationale, since that still avoids the "no magic numbers" problem as long as the derivation is explicit. Alternatively, I could sidestep the gap assumption entirely by setting c = 1/max_open_positions, which naturally caps exposure so that filling all slots stays within 100% equity without leverage.

Weighing the two: the 1/7 approach couples the cap to the position limit, which is logical for diversification but means changing max_open_positions silently shifts risk exposure — though that's still a deliberate human edit, so it's probably fine. I'm leaning toward a separate explicit `MAX_POSITION_PCT` parameter instead, since it avoids hidden coupling, and I need to settle on a default value with clear justification — somewhere around 15-20%.

I should also check whether the 1% risk cap actually binds in practice: with a 15% notional cap and a typical 1.3% stop+cost distance, realized risk is only about 0.2% of equity — far under 1%. So the position-size cap is really the dominant constraint here, not the risk target, and the operator should understand that.

This actually validates the user's intuition — the formula ties risk to the deposit percentage, but with intraday stops it's exposure (position size as share of capital) that becomes the real binding constraint, not the 1% risk target itself.

Что отсутствует — это ограничение по ликвидности, кап на долю от среднего объёма бара или глубины стакана, чтобы проскальзывание не выходило за предполагаемые 5 б.п. В документации есть фильтр ликвидности на уровне пула (средний объём выше 40-го перцентиля, спред ≤0.3%), но глубина стакана не проверена. Для крупных депозитов на ликвидных бумагах МосБиржи участие в объёме незначительно, но для менее ликвидных бумаг из пула позиция в 100к может быть заметной долей объёма.

Единицы Candle.volume в T-Invest API (лоты или штуки) мне точно неизвестны — помечаю как TODO(VERIFY). Добавлю параметр MAX_VOLUME_PARTICIPATION как четвёртое ограничение, с пометкой о неопределённости единиц объёма свечи и предположительным значением по умолчанию около 5% от среднего объёма за 5 минут.

Думаю представить итоговую формулу как минимум из четырёх независимых ограничений — по риску, по лимиту позиции, по доступным средствам и по ликвидности — с масштабирующим коэффициентом для волатильного режима. Нужно аккуратно решить, применять ли этот коэффициент до или после операции минимума, поскольку в текущем коде это сделано иначе.

Если лимит позиции является ограничивающим фактором, уменьшение риск-бюджета может не повлиять на итоговый результат, поэтому для реального снижения экспозиции логичнее применять множитель волатильности к финальному минимуму всех ограничений — это строже соответствует формулировке документа "размер позиции ×0.5" и даёт более консервативный результат.

Для наращивания позиции (50/30/20) полный расчётный размер — это "цель"; первая доля равна floor(0.5 × цель) и так далее. Но риск считается по совокупной позиции, а не по частям, и поскольку доливки происходят по другим (более высоким для лонга) ценам при том же стопе, цена за лот растёт — значит, для каждой доливки нужно пересчитывать оставшийся риск-бюджет (R_remaining = R_total минус убыток по уже открытым долям с учётом издержек) и брать минимум из запланированной доли, оставшегося бюджета и прочих лимитов, иначе фиксированные 30% от плана могут превысить бюджет.

Также если стоп был передвинут в прибыль (например, в безубыток), риск уже открытой части снижается и освобождает бюджет — общая формула это уже учитывает, просто нужно пересчитывать R_open по текущему стопу. Правило масштабирования можно сформулировать компактно: каждый транш считается той же формулой, где бюджет = R минус риск уже открытой части по текущему стопу.

Округление — floor (ROUND_DOWN) в конце, всё через Decimal, согласно AGENTS.md. После округления нужно проверять инварианты (lots × ℓ ≤ R и lots × lot_size × P_e ≤ c × B), и если lots == 0 — возвращать INSUFFICIENT_SIZE с указанием, какое именно ограничение сработало (binding_constraint: risk|cap|funds|liquidity|daily_headroom|drawdown) — это полезно для журнала и оператора, чтобы видеть, чем лимитируется сайзинг, и стоит включить это поле в RiskVerdict/журнал.

Но контракт RiskVerdict фиксирован (allowed, reason, max_lots, checks по docs/01_ARCHITECTURE.md) — добавление поля потребует правки документации, либо можно впихнуть разбивку в reason/checks как рекомендацию.

Главный тест — property-based (hypothesis, 1000 примеров): для случайных входов проверяю, что при исполнении входа и выхода по стопу с учётом проскальзывания реализованный убыток (фактически посчитанный Portfolio из журнала, включая комиссии и слиппедж) ≤ R ≤ r × B, также lots × lot_size × P_e ≤ c × B, cost ≤ F, и худший дневной убыток ≤ d × E_day0. Я замечаю, что текущая формулировка property из AGENTS.md §6 слишком мягкая — она допускает превышение риска за счёт издержек, что с комиссией 0.3% становится существенным, поэтому property нужно ужесточить: риск включая все издержки должен быть ≤ r × B, без исключений.

Проверяю, нет ли противоречия между AGENTS.md и 04_RISK.md: в 04_RISK.md формулировка слабее ("не превышает risk_per_trade больше чем на величину проскальзывания + комиссии"), но это совместимо, если трактовать "risk" в AGENTS.md как планируемый риск без издержек — более строгая новая формула удовлетворяет обеим версиям, так что останавливаться на этом не стоит.

Для golden-тестов продумываю числовой пример: счёт E = 100 000 ₽, без открытых позиций, цена инструмента P = 250 ₽, лот = 10 акций — строю конкретные цифры, чтобы показать разницу между планируемым и фактическим риском с учётом издержек.

Считаю старую формулу: risk_amount = 1000, убыток/лот = 15 → 66 лотов, нотional 165 000 ₽ превышает кэш в 100к, значит по коду это REJECT, а по документации — ограничение до доступных средств, то есть 39 лотов (97 500 ₽, 97.5% депозита). Далее пересчитываю реализованный убыток на стопе для 39 лотов с учётом комиссий и проскальзывания: получается около 1265 ₽, то есть 1.27% от капитала — против 2141 ₽ (2.14%) в сценарии с 66 лотами, если бы хватило маржи, что наглядно показывает, как ограничение по кэшу снижает фактический риск ниже номинального целевого уровня.

Теперь проверяю компактную формулу убытка на единицу через цену входа, выхода, слиппедж и комиссию, получаю то же значение 32.4475225 ₽ на лот, подтверждая расчёт.

Перехожу к новой формуле расчёта размера позиции: беру капитал на стопах B = 100 000, риск R как минимум из трёх ограничений (1% от капитала, дневной лимит, лимит просадки) = 1000 ₽, отсюда N_risk = 1000 / 32.4475225 ≈ 30 лотов. Затем считаю ограничение по концентрации N_cap при c = 0.15 — получаю 6 лотов, и задумываюсь, нужно ли использовать цену исполнения с учётом проскальзывания или просто цену входа — разница незначительна. Далее считаю ограничение по доступным средствам N_funds с учётом резерва 5000 ₽ — получаю 37 лотов, и отмечаю, что ограничение по ликвидности в этом примере пропускаю как несвязывающее. Итоговое число лотов — минимум из всех ограничений.

При подстановке чисел (30, 6, 37) минимум равен 6 — связывающим оказывается именно лимит концентрации, а не риск. Реализованный убыток на стопе при 6 лотах составляет 194.69 ₽, то есть всего 0.19% от капитала, при экспозиции 15%.

Это удивительный результат: ограничение по концентрации доминирует, а риск составляет лишь малую долю от допустимого. Это выявляет структурную проблему стратегии — при стопе 0.6% цены и комиссии 0.3% за сторону издержки (1.5 ₽ из 3.24 ₽ убытка на акцию) составляют около 46% от потерь на стопе. А чистый тейк при 2×ATR оказывается около 0.5 ₽ против убытка 3.24 ₽, то есть соотношение прибыль/риск около 0.15 — крайне плохая экономика сделки, требующая винрейта выше 87% для безубыточности. Это критический сигнал для оператора: при заданных тарифах и параметрах стопов/тейков от ATR(15m) стратегия структурно убыточна, и я должен кратко упомянуть этот вывод, поскольку он напрямую следует из формулы убытка с учётом издержек.

Теперь я уточняю расчёт чистого тейка точнее: с учётом проскальзывания и комиссии на вход и выход цена входа и выхода пересчитывается так, что чистая прибыль на акцию при тейке составляет всего около 0.24 ₽ против убытка 3.24 ₽ на стопе — то есть R/R около 0.075, а требуемый винрейт для безубыточности — порядка 93%. Это ещё хуже, чем первая оценка. Я проверяю, как меняется картина при более низкой комиссии (0.05% вместо 0.3%): пересчитываю цены входа и выхода с новым тарифом, чтобы увидеть, насколько меняется соотношение прибыль/убыток.

Пересчитываю с новой ставкой комиссии чистую прибыль по тейку — получаю около 1.5 ₽ против убытка около 2 ₽, что даёт R/R примерно 0.75 и требуемый винрейт около 57% — заметно более достижимый результат. Это важная находка: экономика стратегии сильно зависит от тарифа, но при этом она зависит и от величины ATR относительно цены — пробую разные сценарии ATR (0.4% против 1% от цены), чтобы показать, как сильно меняется R/R в зависимости от волатильности. Решаю, что стоит упомянуть этот вывод как побочный, но важный практический момент, хотя вопрос пользователя касался именно формулы размера позиции, а не экономики сделки как таковой.

Также обдумываю выбор дефолтного значения доли капитала на инструмент (c) — при 15% риск на сделку получается очень маленьким (0.19%), что безопасно, но консервативно; при увеличении до 50% риск от гэпа может превысить дневной лимит просадки, особенно если открыто несколько позиций одновременно, так что лимит средств должен ограничивать это сверху.

Принципиальный подход: c = d / G_max, где G_max — худшее правдоподобное неблагоприятное движение цены через стоп до выхода из позиции (ночные гэпы, приостановки торгов на MOEX из-за ценовых лимитов). Поскольку точно откалибровать G_max без исторических данных нельзя, я предлагаю дефолт c = 0.20 (из d=3%/G=15%) с пометкой, что это нужно калибровать по максимальным неблагоприятным движениям свечей за N лет, учитывая известные дивидендные гэпы на MOEX в диапазоне 10–20%.

Actually, defining the cap through an explicit gap-risk scenario seems more principled than an arbitrary percentage: the position size should be set so that if price gaps by G against the position, the loss stays within a defined risk budget.  Working through the math, that gives roughly 20% as a cap, and I think presenting it as a simple percentage parameter with the derivation documented is more intuitive for the operator — but I'll flag it explicitly as a proposed default requiring confirmation, consistent with the rule against inventing precise values as if they were facts rather than design choices.

The 20% gap scenario costs exactly 3% of the daily limit with this 0.15 cap, which reinforces the number's validity. But I'm noticing that with c=0.15, the cap tends to bind for most intraday stops anyway, meaning effective per-trade risk works out to roughly 0.15 times the combined stop and cost percentage.

That means the explicit "1% risk" target becomes largely moot — it's really a ceiling imposed by the position-size limit rather than the actual driver, which I should state plainly to the operator so they understand which constraint is actually binding. Though I should acknowledge the operator could always raise c themselves if they want the 1% target to be the active constraint instead.

I'm also checking the daily-headroom cap: with per-trade risk at 0.2–0.4%, seven trades would sum to roughly 2.1%, under the 3% limit, so that cap rarely binds on its own — it only kicks in after the day has already accumulated losses, shrinking remaining headroom for new trades. Now I'm formally defining equity-at-stops as cash plus the sum of position values at their stop levels.

I'm working out the exact formula summing long positions valued at their stop or mark (whichever is worse) net of spread and fees, minus short positions similarly, with any position lacking a defined stop forcing equity-at-stops to be undefined and risk to zero (rejecting the trade). I'm also flagging that how cash is tracked for live broker positions versus the paper portfolio needs verification, and considering whether using min(stop, mark) correctly handles the case where price has already moved past the stop.

I still need to pin down what daily_start_balance actually represents—whether it's cash or full mark-to-market equity at session open isn't currently defined anywhere, since the AccountState structure from the portfolio module hasn't been wired in yet; it should be equity, and I'm noting that gap. Similarly, the launch-time equity baseline for drawdown tracking needs clarifying—whether it's the value at launch or the running peak since launch. Now I'm laying out the full input parameters for the position-sizing formula: expected entry price, the stop price rounded to the tick, lot size, fee rate, and assumed slippage.

Working through the three-step sizing logic: first the base capital figure (cash plus open positions valued at their stops, minus exit costs), then the risk budget as the minimum of the per-trade risk fraction, the remaining daily loss allowance, and the remaining global drawdown allowance—rejecting if this comes out non-positive. Then I'm deriving the per-lot loss when a stop triggers, accounting for slippage and fees on both the entry and exit legs for long and short cases, and rejecting if that loss comes out non-positive since that would mean the stop is on the wrong side of the market.

Now I'm turning to the candidate lot sizes: one driven by the risk budget divided by per-lot loss, one capped by a maximum position percentage of capital, one bounded by available free funds above a minimum balance threshold, and one limited by a max participation share of average volume—though I'm flagging that the units and short-margin treatment on a couple of these still need verification. The final lot count takes the floor of the scaled minimum across all these candidates, applying a volatility size multiplier when relevant, and rejects as insufficient size if it rounds to zero, logging which constraint was binding. I'm pausing to double-check what exactly the average volume term should represent before finalizing that formula.

I'm also working through the scaling-in logic for additional tranches: each tranche's lot count should be capped by its fractional share of the original target position, by the remaining risk budget after accounting for the already-open portion at its current stop, and by the usual position caps—but applied to the combined existing-plus-new notional. The tricky part is whether the risk budget for the ticker should stay fixed at the value computed when the first tranche was opened, rather than being recalculated at each add-on signal.

I'm leaning toward fixing the ticker's total risk budget at the first tranche, then for each subsequent add computing the available room as the minimum of the remaining ticker-level risk, the daily headroom check (recomputed fresh each time), and the position caps—making sure I'm not double-counting the already-open risk when checking against the daily budget, since that open risk is already baked into the equity-at-stops figure.

Now I'm thinking through rounding rules: market-order fills use the best price plus slippage, limit orders use the stated limit price, and for the stop I need to decide whether to round away from or toward entry—rounding away makes the risk-per-share larger (more conservative sizing) while rounding toward entry makes it tighter, but either choice preserves the risk invariant as long as sizing consistently uses the same rounded stop value.

For stops defined by "beyond the candle extremum," rounding toward entry risks landing inside the extremum, which breaks the intended semantics — so I'd recommend always rounding away from entry (down for longs, up for shorts) for both that case and the ATR-based stop, since it's conservative and consistent. I also note P used in the capital calculation should ignore slippage, and that shorts still have an unresolved margin requirement question flagged for verification.

For a no-leverage paper model, I'm trying to define free collateral simply as equity minus gross exposure: for long positions that reduces to cash remaining, but for shorts I need to think through how shorting affects cash and liabilities before settling on the funds check formula.

This is the cleanest no-leverage definition of free funds working for both longs and shorts: F = E − Σ|position_i|. For live trading, the broker's "available for trading" via margin attributes still needs verification, but this formula elegantly fixes the short-inflation bug I was worried about.

For live, I'd cap it at min(that formula, broker-reported available) pending verification. Since shorting inherently involves margin, a no-leverage collateral model still permits shorts up to free collateral, which is conservative but acceptable. Now I should check docs/08 and the API client for liquidity and volume units.

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: docs/08_TINVEST_API.md

Pattern: volume|объём|лот|margin|марж|min_price_increment|currency
Path: docs/08_TINVEST_API.md

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/api_client.py

Pattern: volume|lot
Path: trading_bot/api_client.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: tests/test_portfolio.py

Pattern: open_position\(|lot_size|lots=
Path: tests/test_portfolio.py

---

**Kodik**

[Рассуждение]
I'm noticing the Portfolio's notional calculation uses price times lots without accounting for lot_size, while PaperBroker correctly multiplies filled_lots by lot_size to get units. This mismatch could undercount notional by a factor of lot_size, which is a potential accounting bug affecting the equity base used in sizing formulas — worth flagging even though it's a side observation.

I should also double-check whether slippage is being double-counted in the balance deduction, depending on whether the passed price already includes the slippage adjustment or is just a reference price plus a separate slippage cost. The volume field's units (lots vs shares) also need a TODO(VERIFY) tag since I can't confirm the T-Invest API semantics without documentation.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/06_DB_SCHEMA.md

Path: docs/06_DB_SCHEMA.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/11_TESTING.md

Path: docs/11_TESTING.md

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: docs/15_ACCEPTANCE.md

Pattern: M5|риск|Риск
Path: docs/15_ACCEPTANCE.md

---

**Kodik**

[Рассуждение]
I want to verify the config file's risk settings, checking for the default risk per trade and whether a max position percentage field exists.

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/config.py

Pattern: risk_per_trade|max_position|global_drawdown|position_timeout|scaling_in|min_free_balance|fee_rate|slippage
Path: trading_bot/config.py

---

**Kodik**

[Рассуждение]
Now I have all the evidence gathered, so I'll move to writing the final answer in Russian, double-checking the example calculation with precise arithmetic before presenting it.

Using the old risk formula from the docs, I calculate 66 lots and a notional value of 165% of the deposit, which triggers a rejection in the actual code due to insufficient funds — contradicting what the documentation implies about automatically reducing lot size. I'm working out the per-unit loss at stop including slippage and commission costs to compare against the simplified doc formula.

Breaking down the actual stop-loss cost, I find that fees and slippage account for 54% of the total loss per share while the raw stop distance is only 46% — meaning if 66 lots were actually allowed, the real loss would be 2.14% of the deposit instead of the intended 1%, more than double the target risk.

Testing the new formula with a fresh 100,000 deposit: R comes out to 1,000 (the 1% cap binds), giving N_risk of about 30 lots, while the capital-based cap (15% rule) limits it to just 6 lots.

Checking N_funds, the funds-based limit allows up to 37 lots, so the binding constraint is the 15% capital cap at 6 lots — loss at stop would be about 194.69 ₽, just 0.19% of deposit, though exposure sits at 15%.

I'm wondering if loosening the cap to 20% changes things meaningfully — that would allow 8 lots and 0.26% loss, but let me think about whether there's a better way to balance these constraints.

Now I'm testing a wider stop scenario with ATR=5 to show a case where risk actually binds instead of the capital cap — working through the price math to get the per-lot loss at around 92.24 ₽, giving 10 lots under the new formula versus 13 under the old one, where the old approach overshoots to 1.2% actual loss.

With cap c=0.15, the notional cap still binds at this ATR level, which reveals an important interplay: the risk constraint only kicks in when stop+costs exceed roughly 6.67% of price (r/c), meaning with typical costs around 0.7% the stop would need to exceed ~6% of price — requiring ATR beyond the 5% max_atr_ratio ceiling. This means with c=15%, the 1% risk limit is essentially irrelevant and sizing is driven almost entirely by the notional cap, except for unusually volatile instruments.

I'm also recognizing the deeper tension: tight intraday stops combined with 1% risk sizing naturally pushes position sizes toward the notional cap, so the choice of c effectively decides whether the system behaves as risk-based or notional-based. The whole purpose of fixed-fractional risk sizing is to equalize the dollar loss-at-stop across trades for consistent statistics and drawdown control, but that only works if the stop is actually honored — and gap risk undermines that assumption.

Given 15-minute ATR stops, risk-based sizing would often demand 50-150% of equity, which is leverage-heavy and exposed to gaps — so any reasonable notional cap will almost always be the binding constraint, making the effective risk per trade something like c times (stop% + cost%), e.g. 15% × 1.3% ≈ 0.2%. That's conservative, but it means the stated "1% risk" is really just a ceiling rarely hit in practice, which matters for how the operator interprets trade-level outcomes.

Since the cap usually binds, risk equalization across trades is lost — wider-stop positions will lose more in absolute terms. I'm weighing a hybrid sizing rule that takes the minimum of risk-based and cap-based sizing, and considering what happens if the operator instead picks c to hit 1% risk at a "typical" stop width: that pushes c to around 77%, which would expose 7.7% of equity to a single overnight gap given a 10% gap assumption — far too dangerous against a 3% daily loss limit. So my honest recommendation settles on c around 15-20%, defaulting to 15%, while being upfront that the cap will usually be what actually constrains position size.

I want to frame this as an intentional tradeoff rather than a flaw: with tight intraday stops, hitting the full 1% risk target would mean sizing the whole deposit into one position, so the cap is doing necessary work. For the 0.15 default I'm leaning on two justifications — it lets roughly seven uncorrelated positions fit within the deposit without leverage, and it caps the loss from a single-name 20% gap at 3%, matching the daily limit — though I'm second-guessing whether a 20% single-name gap is a realistic enough tail event to anchor the default on, since the plan document treats 3%+ gaps as already notable.

I'll phrase the gap property as a structural guarantee rather than a frequency claim, and expose it as one parameter (MAX_POSITION_PCT, default 0.15) with that documented behavior rather than overcomplicating it with a separate tail-risk formula. Turning to the daily headroom term, with the 15% cap binding and typical per-trade risk of 0.2–0.5%, seven positions would sum to 1.4–3.5% total open risk, so the headroom constraint will still bind after losses or with wide stops — which is correct, since it's meant to be the true hard limit. I also want to note that because the headroom calculation uses unrealized P&L, positions sitting in profit with stops trailed to breakeven actually free up more room, which makes sense since that risk is already locked in.

Now I'm tracing through whether `balance_free` correctly excludes open position value for shorts — checking `Portfolio.open_position`'s short-side accounting against the balance docstring, which claims the free balance should never include open position cost. The wiring from `AccountState.balance_free` back to `Portfolio.balance` isn't actually built yet in the executor, so this is a latent bug: if that wiring goes in as currently documented, shorting 50k would inflate balance_free to 150k and overstate the next risk_amount. I want to flag this with the specific line references in portfolio.py and risk_manager.py as evidence, and also check whether buying long correctly shrinks balance_free the way it should.

I'm also realizing that after one long position eats most of the balance_free down to ~2.2k, the minimum free balance check (5000) would block every subsequent trade — so in practice only one position can ever be open at a time, which defeats the purpose of having multiple risk slots. On top of that, I'm spotting a possible lot_size bug: Portfolio computes notional as price times lots without multiplying by lot_size, while PaperBroker correctly multiplies lots by lot_size to get units — meaning if price is quoted per share, Portfolio would undercount notional by a factor of lot_size, creating a mismatch between the fee (computed on full units) and the cash deduction (computed on undercounted notional).

I should flag this as unresolved — whether `price` in `Portfolio.open_position` is meant to be per share or per lot isn't documented, and the tests don't disambiguate it since they use lot_size=10 alongside price=100 either way. This matters because it directly affects equity calculations which feed into the sizing formula, so I want to note it concisely as a possible issue needing verification. There's also the separate issue that `_check_size_and_funds` rejects trades outright instead of reducing position size, which contradicts what docs/04_RISK.md describes — check 12 failing means allowed=False and final_lots=0.

Beyond that, I'm noticing a mismatch between the ranging stop placement (beyond the candle extremum per docs) and the sizing logic which always uses ATR×1.5 — if the extremum stop sits farther than 1.5 ATR, realized risk exceeds the intended budget. Compounding this, `Signal` doesn't carry a stop price at all, so the risk manager has no way to know the actual planned stop; the new formula needs the stop price S as input, meaning this is really an interface gap requiring either `Signal` to carry `stop_price` or the risk manager to compute the stop itself from a stop rule and the candle extremum — which raises a design question about who should own stop-placement decisions.

I'm also realizing the existing property test for risk sizing is somewhat circular, since it just recomputes the same formula without accounting for costs, so it wouldn't catch this kind of discrepancy. A better test should verify risk independently by actually opening and closing positions through PaperBroker and Portfolio rather than re-deriving the same formula.

I should also briefly address whether the volatility multiplier and scaling-in logic belong in this formula, and confirm the risk-per-trade warning threshold stays as-is. Since I'm in Ask mode, I can't edit files directly, so I need to frame my response as a design proposal, stating the stage (M5 risk formula), the task, and the acceptance criterion from docs/15_ACCEPTANCE.md.

Implementation, changelog updates, and any changes to docs/04_RISK.md will need Code mode and operator sign-off, since the existing formula was locked in by an earlier operator decision recorded in the CHANGELOG — the new formula would supersede that, so it needs explicit confirmation before I log it. I also want to double check whether the new formula could ever produce a larger position size than the old one in any risk state, comparing the two formulas directly.

Working through a concrete example with a 50k long position, comparing old vs new lot calculations: with balance_free at 50k but equity stops near 99k, the new formula could allow for roughly double the risk budget, though the actual lot difference depends on how much the per-lot cost eats into the stop distance.

Checking whether the position cap binds: with a 6% stop distance, the new formula allows roughly 14.5k notional versus the old formula's 8.3k, confirming planned risk jumps from 0.5% to nearly 1% — so in this scenario the new approach is actually taking on more risk than the old one, not less, contrary to my earlier assumption.

Per AGENTS.md §4.2, risk parameter changes require explicit human approval via config edit and restart, so even though this formula change is operator-approved, I need to flag clearly that the new formula can sometimes allocate more risk than the old one did on second-and-later positions (since the old one calculated against a shrinking cash remainder) — this isn't a rule violation since per-trade risk still stays within the configured RISK_PER_TRADE ceiling, but transparency about the behavioral shift matters.

This means later positions can legitimately get bigger budgets than the old formula gave them, and that's intentional since 1% of capital should mean 1% of capital—though it needs confirmation. Now I'm thinking about whether to include the liquidity term N_liq, since it's needed to justify the 5bps slippage assumption, which depends on average volume from the 5-minute candle data.

For the MAX_VOLUME_PARTICIPATION parameter, I want to avoid a magic number so it should live in config with a sensible default—maybe 5% of the average 5-minute bar volume, following typical execution rules of thumb where algos cap participation around 10-20%, making 5% a conservative choice for a single market order. I'll flag it as a starting value that should be calibrated empirically rather than treated as fixed truth.

Thinking about the liquidity constraint term, I'm weighing whether to keep it as a secondary, optional factor since liquid MOEX names rarely bind on it, but second-tier names above the 40th liquidity percentile can have thin books where it matters. I'll keep it in the formula but mark it as a lower-priority term pending volume-unit verification.

I'm also reconsidering how many new parameters to introduce — settling on MAX_POSITION_PCT as essential, STOP_SLIPPAGE as optional with a default tied to SLIPPAGE_ASSUMED, and MAX_VOLUME_PARTICIPATION as a second-order addition, distinguishing these as design choices rather than unverified API facts.

Now I'm planning the answer structure in Russian — a concise verdict that the user's claim is only half right, since the deposit-share link genuinely applies to risk sizing (risk_amount = balance_free × risk), but not necessarily to other parameters the way they assumed.

Then I'll break down what's actually wrong into five concrete gaps, each backed by evidence and a number — starting with the fact that the base used is free cash rather than total capital, which shrinks with long positions and inflates with short positions.

Second, there's no cap on position size as a share of deposit: with a tight stop the formula can demand 165% of deposit, and the code rejects this outright rather than scaling down as the docs suggest — so it's either reject-everything or all-in, with no middle ground.

Third, trading costs sit entirely outside the formula — at a 0.3% fee they amount to 54% of the loss at stop in my example, turning a supposed 1% loss into an actual 2.14% loss.

Fourth, the stop used in the sizing formula doesn't match the real stop: in ranging markets the actual stop sits beyond the extremum, but sizing always uses a fixed 1.5×ATR, and the Signal object itself carries no stop information at all.

Fifth, there's no link to the daily loss limit — seven positions at 1% each could total 7% loss against a 3% daily cap, and the existing check only blocks new trades after the damage is done.

I'm now laying out the rest of the plan: presenting the actual formula with proper notation, then an example comparing old vs. new behavior side by side, followed by a breakdown of what changes in practice — noting the cap will usually be the binding constraint, that 1% is a ceiling not a target, that later positions might get a larger budget than before (needs confirming), and the real economics of trading costs. I also want to list the tests that would validate this — a property-based test using PaperBroker and Portfolio as an independent check, a golden five-tick test, and targeted tests for short positions not inflating the base size, positions without stops defaulting to zero, remaining daily risk headroom, and ranging-market stops. Then I need to specify new parameters with TODO(VERIFY) markers, and finally map out the process: since I'm in Ask mode I can't edit anything, so I'll need Code mode for the actual changes, update the relevant docs (04_RISK.md, 00_PLAN.md §7, 07_CONFIG_SPEC.md, 01_ARCHITECTURE.md for Signal/RiskVerdict), log this in the CHANGELOG as superseding the 2026-10-14 decision, and ask the operator to confirm the default value for c.

I'm drafting the response now — opening with the stage, task, and acceptance criteria per the AGENTS.md protocol, noting honestly that in Ask mode I haven't changed anything yet, and ending with the one concrete decision the operator needs to make: confirming MAX_POSITION_PCT (I'd recommend 0.15) and approving the formula replacement. I want to keep this concise rather than exhaustive, starting by acknowledging that the risk-to-balance link does exist in the current formula, then explaining how the per-lot sizing diverges from that.

I'm identifying three real gaps: the formula uses `balance_free` instead of capital, which shrinks on longs and inflates on shorts (since the Portfolio class adds short proceeds back to balance, skewing the next trade's risk budget); the position size isn't capped as a share of the deposit, so a tight stop can request a position larger than the entire account, and instead of scaling down lots as the docs intend, the risk manager zeroes them out entirely — leading to either a flat rejection or, per the docs, the whole deposit concentrated in one instrument while six other slots sit idle; and fees aren't accounted for in the formula at all, so a 0.3% fee rate isn't just a rounding tolerance but a real cost eating into the risk budget.

I also note the stop price used in the formula doesn't match the strategy's actual stop placement logic (ATR-based vs. candle-extreme), the Signal object doesn't even carry a stop price, and there's no connection between per-trade risk and the daily loss limit — seven positions at 1% each could exceed a 3% daily cap before any check catches it. Now I'm looking at the proposed formula notation defining entry price, stop price, lot size, fee rate, and slippage assumption as the basis for a corrected calculation.

Writing risk budget formula...

Still deriving position caps...

Фиксируется на первом транше, а каждый следующий транш пересчитывается как минимум из доли плана, оставшегося бюджета риска относительно уже открытой позиции по её стопу, делённого на новый объём от новой цены входа, и остатков потолков — иначе докупка выше входа незаметно увеличит риск.

Первый шаг единым числом закрывает три проблемы: база не сжимается от лонгов и не раздувается от шортов, а нереализованная прибыль учитывается только в части, защищённой стопом. Второй шаг превращает дневной лимит и глобальную просадку в настоящую верхнюю границу до гэпа, а не предохранитель постфактум. Третий шаг воспроизводит модель `PaperBroker` один в один с учётом комиссии и проскальзывания, что позволяет точно проверить инвариант.

Теперь я продумываю пример: депозит 100 000 ₽, цена 250, стоп по 1.5×ATR даёт убыток на лот около 32.45 ₽ с учётом издержек против прежних 15 ₽ только по стопу, из-за чего старая формула давала 66 лотов (165% депозита), а новая ограничивает размер позиции совместно риском и лимитом.

С учётом реальных издержек в 32.45 ₽/лот убыток при 39 лотах составляет 1 265 ₽ (1.27%), а при гипотетических 66 лотах вышло бы 2 142 ₽ (2.14%) — издержки съедают больше половины убытка на стопе. Пересчитываю: по риску получается 30 лотов, по капиталу (15%) — 6 лотов.

Строю сводную таблицу для сравнения старого и нового подхода — убыток на лот, формула расчёта количества лотов, итоговое число лотов и убыток на стопе в рублях и процентах, проверяя арифметику (39 × 32.4475225 ≈ 1265.45 ₽).

Проверяю остальные расчёты: при 66 лотах убыток 2142 ₽ (2.14%), при 6 лотах — 195 ₽ (0.19%), доля издержек в убытке на лот составляет 53.8% (округляю до 54%), издержки на лот — 17.45 ₽. Теперь разбираюсь в компонентах этой суммы: движение цены от входа до стопа даёт 15 ₽ на лот, плюс проскальзывание на обеих ногах сделки.

Прихожу к ключевому выводу: ограничивающим фактором оказался потолок доли позиции, а не целевой риск 1%. Это логично — при стопе 0.6% цены и издержках 0.7% честный риск в 1% потребовал бы позиции в 77% депозита, а риск достигает заявленного 1% только при очень большом стопе с издержками (около 6.7% цены), что почти никогда не выполняется при текущих ограничениях по ATR. Значит, 1% риска — это верхний потолок, а не фактическая цель, и реальный риск сделки обычно составляет 0.15–0.5%.

Теперь проверяю экономическое следствие этой логики: беря 2×ATR в примере, чистая прибыль около 0.24 ₽/акцию против убытка 3.24 ₽/акцию — соотношение прибыль/риск около 0.075, что требует винрейта выше 93% для безубыточности. Это довольно тревожный вывод, но он зависит от конкретного ATR в примере. В общем виде: чистое соотношение R/R ≥ 1 требует ATR ≥ 4 × издержки ≈ 2.8% от цены — это близко к границе фильтра волатильности в 5%, то есть стратегия работает только на очень высоковолатильных акциях.

Рассчитываю пороговый win-rate для разных уровней ATR: при A=0.4%P нужен win-rate ~93% (сходится с эмпирикой), при A=1%P — ~63%, при A=2%P — ~53%. Также проверяю, как меняются издержки при более низкой комиссии подписки 0.05%, что снижает совокупные издержки до 0.2%P.

Это наблюдение важно подчеркнуть отдельной строкой: при тарифе 0.3% и ATR(15m)=0.4% цены требуется win-rate ≥93%, чтобы выйти в ноль — и это не решается просто изменением размера позиции. Стоит добавить это как короткую побочную заметку, вынесенную за рамки основного вопроса, с оговоркой, что расчёт упрощённый и не учитывает частичный выход позиции (50/30/20 на разных уровнях), который меняет итоговую математику R/R.

Второй побочный момент — расхождение в учёте лотности между `Portfolio.open_position` (считает notional без lot_size) и `PaperBroker` (учитывает lots × lot_size). Если исполнитель передаёт цену за одну бумагу, это занижает или завышает кэш и капитал B в lot_size раз — для лонгов капитал скорее завышается, для шортов нужно проверить направление искажения отдельно — и это стоит исправить до внедрения новой формулы.

Третий момент — интерфейс `AccountState` сейчас не содержит нужных данных: снимков открытых позиций со стопами и текущими отметками, а также стартового капитала E_start. Правильнее, чтобы портфель (L2) передавал снимок позиций с кэшем, а риск-менеджер (L3) вычислял B как чистую функцию от этого снимка — так проще тестировать и сохранить разделение уровней.

Теперь думаю, что `AccountState` должен нести открытые позиции (сторона, объём, стоп, текущая цена) + кэш + E_day0 + E_start, а сам B считается внутри risk_manager — единое место для формулы. Плюс сигналу/`InstrumentState` нужны экстремум входной свечи, тик и плановая цена входа, а стоп тоже вычисляется в risk_manager — это стоит кратко отразить в разделе "что поменяется в коде".

По тестам: главное свойство через 1000 hypothesis-примеров нужно проверять независимым путём — PaperBroker открывает лоты по цене P, закрывает по S, Portfolio фиксирует сделку, и должно выполняться −pnl_net ≤ R ≤ RISK_PER_TRADE·B, без допуска на издержки, что строже чем в текущей документации по тестированию, но соответствует требованиям AGENTS.md §6.

Проверяю формулировку в документации — там указан допуск на издержки, то есть новое условие строго сильнее и с ним совместимо. Но возникает вопрос: будет ли pnl_net, посчитанный через модель PaperBroker с проскальзыванием и комиссиями на входе и выходе, реально укладываться в это неравенство — если в Portfolio есть возможное двойное вычитание проскальзывания (когда цена уже фил-цена, а slippage ещё раз вычитается), то pnl_net окажется более отрицательным, чем предполагает формула, и тест упадёт, что на самом деле хорошо — это выявит реальный дефект учёта, а не ошибку формулы. Стоит отметить, что если тест падает из-за Portfolio, это найденный баг учёта, а не проблема самой формулы риска, и что проперти-тест, проходящий через PaperBroker и Portfolio, заодно поймает несостыковку с lot_size/units. Теперь хочу перепроверить именно механизм двойного учёта проскальзывания при закрытии позиции в Portfolio.

Разбираюсь, как считается pnl_net: gross минус сумму комиссий и проскальзывания на входе и выходе, и если исполнитель передаёт цену как уже слипнутую fill-цену вместе со slippage_cost, то slippage считается дважды; а если передаётся референсная цена вместе со slippage_cost, баланс балансируется корректно, потому что notional считается по референсной цене. Значит правильная конвенция — референсная цена плюс отдельная стоимость проскальзывания, это контракт исполнителя (M8), и я решаю не трогать это сейчас, а просто зафиксировать в заметке про Portfolio, что единица и смысл поля price (за бумагу или за лот, с проскальзыванием или без) не определены в контракте. Дальше перехожу к второму пункту — ручному пересчёту золотых пяти тиков с учётом издержек, включая разобранный выше пример, плюс сценарий с коротким рейнджем стопа.

Продумываю дизайн: пять золотых тиков, где каждое ограничение (риск, лимит капитала, лимит средств, ликвидность, дневной запас) срабатывает ровно по одному разу — это выглядит как элегантная конструкция теста. Для изолированных тестов прикидываю конкретные числа: шорт не должен увеличивать баланс B; позиция без стопа должна отклоняться; для R_day считаю, что при дневном убытке 2.9% и открытом риске 0.2% бюджета не остаётся — равновесие даёт B=96.9k против E_day0_remainder=97k, то есть R_day=-0.1k и сделка отклоняется. Также отмечаю, что волатильность уполовинивает финальный лот, а добавочные сделки никогда не должны поднимать совокупный риск выше R.

Замечаю, что текущий property-тест в tests/test_risk_manager.py (строки 174-197) тавтологичен и его нужно заменить. Для новых конфиг-параметров предлагаю MAX_POSITION_PCT=0.15 (обоснование: 7×15%≈100%, без плеча влезают все слоты, а разрыв 20% против одной позиции стоит не больше 3% — дневного лимита) и прикидываю MAX_VOLUME_PARTICIPATION≈0.05, которую нужно будет откалибровать по фактическим данным.

Оставляю TODO(VERIFY) по единицам Candle.volume (лоты или штуки) — не подтверждено в docs/08, и по margin/free funds для live и шорт-коллатерала, ссылаясь на строку 33 docs/08_TINVEST_API.md про маржинальные показатели (пункт 12, хотя CHANGELOG называл его пунктом 11).

Для бумажного шорт-коллатерала пока принимаю допущение 100% без плеча — это заложено в формуле F = E − Σ|MV|.

Отмечаю поведенческие последствия для подтверждения: формула заменяет решение оператора от 2026-10-14 (нужна явная фиксация в CHANGELOG), для второй и последующих позиций бюджет может оказаться больше, чем по старой формуле — это корректно по смыслу (1% капитала), но является увеличением относительно текущего поведения, значит по AGENTS.md §4.2 требуется решение человека. Также думаю про дневной запас риска: пока стопы не на безубытке, число одновременно открытых позиций с полным риском ограничено 3%, и MAX_OPEN_POSITIONS = 7 остаётся лимитом, но может быть недостижим.

Документы для обновления в Code-режиме: 04_RISK.md, 00_PLAN.md §7, 07_CONFIG_SPEC.md, 11_TESTING.md п.3, 01_ARCHITECTURE.md. В коде — risk_manager.py с новой сигнатурой через dataclass (AGENTS.md §5 про более 5 аргументов), расширение AccountState, переделка проверки 12, тесты.

Проверяю, не упустил ли что-то в "лучшей формуле" — думаю про корреляционно-скорректированный сайзинг поверх существующего жёсткого фильтра корреляции 0.85 и лимита 2 на сектор.

Прихожу к выводу, что дневной лимит headroom уже покрывает худший случай (все стопы сработали) независимо от корреляции — поэтому отдельный корреляционный терм избыточен для жёсткой границы, и это стоит упомянуть одной строкой как объяснение, почему его нет.

Также обдумываю, почему сознательно не беру Kelly/optimal-f — нужна надёжная оценка edge, которой пока нет, а ошибка в оценке переразмеряет позицию; fixed-fractional с капами надёжнее. То же самое касается таргетирования волатильности — оставлю по одной-две строки в разделе "чего сознательно нет".

Добавляю финальные правила округления (ROUND_DOWN, промежуточные Decimal, округление стопа от входа), валютные ограничения (всё в RUB, иные валюты — отказ без конвертации), и уточняю определение E_day0 как капитала на начало торгового дня, а не кэша, с учётом границы торгового дня MOEX. Также фиксирую, что при отсутствии стопа по позиции B не определён — значит отказ, а доступные средства F в live-режиме не должны превышать то, что реально показывает брокер.

Для шорта логика обеспечения аналогична лонгу (100% обеспечение без плеча), пока нет точной маржинальной формулы. Выстраиваю структуру ответа на русском — сжато, по делу, с заголовками, кодовыми блоками и точными ссылками на строки в файлах (docs/04_RISK.md и docs/03_STRATEGY.md), плюс пометка, что в режиме Ask файлы не менялись и CHANGELOG_AI.md не трогался — это появится вместе с реализацией, завершая ответ followup-блоком.

Продолжаю сверять конкретные ссылки на строки в risk_manager.py, portfolio.py, paper.py, strategy.py, тестах и конфиге, чтобы каждое утверждение было подтверждено номером строки.

Теперь проверяю логику ATR(15m) из strategy.py:253 — снапшот содержит atr_15m, но проводка в риск-менеджер ещё не реализована. Также подтверждаю, что при недостатке свободного баланса `_check_size_and_funds` возвращает False, что приводит к полному отказу сделки (final_lots=0), а не к частичному исполнению.

Теперь думаю о том, что B (капитал на стопах) использует текущие маркировки M для минимума/максимума: для лонга со стопом ниже маркировки min(S, M) = S, значит всё корректно. Также рассматриваю, что использование B вместо E_day0 в первом члене R означает лёгкое уменьшение бюджета для 7-й сделки при открытых 6 позициях — это анти-концентрационный эффект, который устраивает. Отмечаю, что B зависит от стопов открытых позиций, и при трейлинге стопов вверх B растёт, увеличивая бюджеты — это компаундинг зафиксированной прибыли, что приемлемо. Думаю, не стоит ли переименовать термин "капитал на стопах" во что-то более точное, вроде "капитал при текущих стопах".

Решаю оформить формулу в текстовом псевдокоде в стиле документации, с аннотациями, связывающими каждый член формулы с конкретным пробелом (#1–#5) из списка проблем, чтобы не дублировать объяснения. Также начинаю готовить список того, что сознательно не включено в модель, начиная с Kelly/optimal f.

Добавляю, что корреляционная поправка не нужна — член [5] уже покрывает худший случай одновременного срабатывания всех стопов, что охватывает любую корреляцию. Параллельно набрасываю заметку об экономике издержек: при комиссии 0.3% и ATR(15m) 0.4% тейк на 2×ATR даёт около 0.24₽ чистыми против 3.24₽ убытка на стопе, показывая асимметрию.

Проверяю расчёт: безубыточный win-rate получается около 93%, что означает положительный R/R только при ATR ≥ 2.8% цены. Это не решается формулой размера позиции — нужно менять тариф (0.05% с подпиской) или таймфрейм стопа, и это стоит вынести отдельным пунктом до M8.

С подпиской (0.0005 комиссия) при A=0.4% безубыток падает до ≈57% — пересчитываю точно: вход 250 × 1.0005 × 1.0005, выход по стопу 248.5 × 0.9995 × 0.9995, убыток получается около 1.998.

Теперь перехожу к решениям, которые нужно зафиксировать оператору: подтвердить замену формулы вместо решения от 2026-10-14 с правкой в CHANGELOG, и определиться по MAX_POSITION_PCT — 0.15 рекомендую против 0.20 (где гэп 15% даёт 3% на 8 лотах).

Также нужно отметить, что вторые и последующие позиции могут получить больший бюджет, чем сейчас — это нужно подтвердить отдельно.

В конце дам короткую фразу от лица пользователя для подтверждения: "Подтверждаю формулу с MAX_POSITION_PCT=0.15 — реализуй её в Code-режиме вместе с доками и тестами". Ответ выйдет длинным, но раз пользователь просил "лучшую формулу" — углублённость уместна, постараюсь держать каждый раздел компактным.

Начинаю писать финальный ответ на русском, открывая его указанием этапа, задачи и критериев приёмки — М5, формула размера позиции, приёмка по docs/15_ACCEPTANCE.md (golden-тест на 5 тиках плюс свойство "риск ≤ заданного" на 1000 случайных наборах), и подтверждением, что в режиме Ask файлы не менялись.

Формулирую тезис: привязка риска к депозиту действительно есть через `risk_amount = balance_free * risk_per_trade`, но в расчёте участвует не капитал, а свободный кэш — это первый из пяти пробелов, которые я дальше буду последовательно разбирать.

Дальше прохожусь по оставшимся четырём пробелам: шорты раздувают базу через зачисление выручки в баланс; размер позиции не ограничен долей депозита — формула режет только убыток на стопе, но не сумму в одной бумаге, при этом код вместо урезания лотов (как требует документация) просто отказывает целиком; торговые издержки не учтены в формуле, хотя при текущей комиссии они съедают больше половины убытка на стопе, а property-тест это не ловит, поскольку сверяет формулу саму с собой без издержек; наконец, в расчёте размера позиции используется не тот стоп — всегда 1.5×ATR, хотя для ranging-режима стоп определяется иначе, а в самом сигнале цены стопа вообще нет. Завершаю указанием, что отсутствует и связь с дневным лимитом риска — семь позиций по 1% дают до 7% суммарного возможного убытка.

Теперь выписываю саму формулу: вход `P`, стоп `S` — реальная цена заявки (по ATR или экстремуму, округлённая до шага цены), лотность `n`, комиссия `f`, проскальзывание `s`, всё в Decimal и в рублях, с отказом для не-рублёвых инструментов. Расписываю базу расчёта как капитал «на стопах» — сколько останется, если сработают все открытые стопы прямо сейчас, с отдельными слагаемыми для лонгов и шортов и отказом при отсутствии стопа у любой открытой позиции. Затем формулирую бюджет риска как минимум из трёх ограничений — стандартный риск на сделку, запас до дневного лимита потерь и запас до глобальной просадки — с отказом при неположительном результате, и перехожу к расчёту убытка на лот с учётом комиссии и проскальзывания.

Дальше выписываю убыток на лот отдельно для лонга и шорта, с отказом, если стоп оказался не с той стороны цены, затем формулирую четыре потолка в лотах — по риску, по доле депозита в одной бумаге, по свободным средствам и по участию в объёме торгов — и итоговую формулу лотности с округлением вниз и масштабирующим коэффициентом для волатильных инструментов, где нулевой результат означает отказ с фиксацией в журнале того, какой именно потолок оказался связывающим. Поясняю смысл: первый пункт закрывает пробел с базой расчёта, не давая лонгам её сжимать и не позволяя нереализованной прибыли раздувать базу за пределами защищённой стопом части, а пятый превращает дневной лимит и глобальную просадку из постфактумного предохранителя в настоящую верхнюю границу, рассчитанную на худший случай одновременного срабатывания всех стопов.

Третий пункт проверяю на соответствие модели PaperBroker, где комиссия берётся уже с цены после проскальзывания, поэтому инвариант можно проверять строго без допуска. Свободные средства считаю как капитал минус сумма модулей открытых позиций, без плеча, с полным обеспечением под шорт, оговаривая, что маржинальные показатели для live пока не подтверждены. Для масштабирования входа 50/30/20 продумываю формулу докупки, где каждый следующий транш ограничен долей плана, оставшимся риском от нового стопа по текущей цене и остатками потолков — иначе докупка выше входа незаметно нарастит риск. Сознательно отказываюсь от Kelly или optimal-f, поскольку они требуют надёжной оценки преимущества стратегии, которой пока нет без статистики paper-торговли, а ошибка в такой оценке ведёт к переразмеру позиции. Теперь перехожу к числовому примеру со старой и новой формулами на депозите 100 000 рублей.

Сравниваю расчёт лотов по старой и новой формуле: при учёте издержек убыток на лот вырастает с 15 до 32.45 рубля, из-за чего итоговый размер позиции падает с десятков лотов до 6 (ограничение по доле депозита), а реальный убыток на стопе снижается с 1.27% до 0.19%. Из этого делаю вывод, что при внутридневных стопах лимитирующим почти всегда будет потолок доли депозита, а не целевой 1% риска — риск-потолок по 1% срабатывает только при стопе с издержками от 6.7% цены, что практически не встречается при ограничении ATR в 5%, так что реальный риск сделки обычно будет около 0.15–0.55%. Также отмечаю, что включение издержек в формулу хорошо показывает экономику самой стратегии — на этом примере тейк 2×ATR даёт небольшую чистую прибыль на бумагу.

Дальше набрасываю план тестов: главное свойство — проверка через PaperBroker и Portfolio, что чистый убыток по сделке не превышает риск-лимита, причём строже, чем в текущей документации по тестированию; golden-тест на пяти вручную посчитанных тиках, где каждый раз лимитирующим оказывается другой фактор (риск, доля, деньги, ликвидность, дневной остаток), включая шорт и стоп за экстремумом; и набор изолированных тестов — на то, что шорт не увеличивает балансовую базу, что позиция без стопа отклоняется, что при просадке дня и открытом риске сделка блокируется, и что транш-скейлинг не поднимает совокупный риск выше лимита. После этого начинаю перечислять, какие новые параметры нужно внести в конфигурационную документацию перед внедрением.

Дальше фиксирую конкретные значения параметров и причины их выбора, отмечаю пункты TODO(VERIFY) по единицам измерения объёма свечи и по марже в боевом режиме, нахожу дефект в расчёте notional-стоимости — Portfolio считает её без учёта лотности, а PaperBroker иначе, и неясно, что именно означает цена в Portfolio, что как раз должен поймать первый тест. Затем перехожу к интерфейсам: AccountState должен включать кэш, открытые позиции со стопами и ценами, начальный и стартовый капитал, а расчёт стопа должен выполнять risk_manager.

py по единому правилу — на основе ATR или экстремума с шагом цены, чтобы размер позиции и сама заявка использовали одинаковое значение S, и поскольку аргументов больше пяти, вход должен оформляться через dataclass согласно AGENTS.md.

Затем формулирую решение: подтверждаю замену старой формулы расчёта риска на новую, фиксируя это в CHANGELOG с оговоркой, что бюджет для второй и последующих позиций может увеличиться по сравнению с текущей логикой, поскольку старая формула считала от остатка кэша — это смысловое улучшение, но по AGENTS.md такое увеличение риска требует решения человека. Выбираю MAX_POSITION_PCT=0.15 как рекомендуемое значение (против альтернативы 0.20), и отмечаю, что для внедрения нужен переход в Code-режим с правками документации, конфигурации, risk_manager.py, тестов и записи в CHANGELOG.

Проверяю арифметику для обоснования выбора 0.15: при коэффициенте 0.15 гэп в 20% против одной позиции обходится не более чем в 3% капитала, а семь открытых позиций по 15% в сумме дают 105% — чуть больше портфеля без плеча, что настораживает. Рассматриваю альтернативу 0.14, которая укладывается в 98% для семи позиций и даёт гэп-риск 2.8%, но это выглядит менее круглым числом, поэтому думаю, как лучше сформулировать это ограничение — возможно, через связь с MAX_OPEN_POSITIONS, а не как жёсткое совпадение.

Также перепроверяю граничные условия: когда риск-потолок связывается с лимитом позиции, реальный риск на сделку колеблется в диапазоне 0.15–0.55% в зависимости от размера стопа, а расчёт лотов — что старый подход даёт отказ при превышении капитала, а новый — 39 лотов с учётом комиссии (97.5% капитала), и свежий пример с убытком на лот при стопе 15 рублей плюс издержки дают N_risk≈30 и N_funds≈37.

Дальше сверяю ссылки на документацию: строгость превышает требования 11_TESTING.md и совпадает с AGENTS §6, проверка №5 блокирует только новые сделки постфактум, Portfolio корректно зачисляет выручку от шорта в баланс, Signal не несёт цену стопа, а 04_RISK.md предписывает урезание — всё сходится с кодом построчно.

Проверяю маппинг меток [1]-[5] в формуле бюджета риска: базовый риск, потолок позиции, издержки, фактический стоп и дневной лимит — расставлены корректно, соответствуя структуре блока "Потолки".

I should clarify that S is the stop price rounded away from entry (down for longs, up for shorts), and note that non-RUB instruments get rejected per the BROKER_CURRENCY=RUB setting in the config spec.

The price parameter in calculate_position_size is only used for validation, not relevant to mention. I'm ready to write the final tightened answer now.

---

**Kodik**

**Этап M5, задача — формула размера позиции. Критерий приёмки — docs/15_ACCEPTANCE.md, раздел M5:** golden-тест на 5 тиках и проверка «риск ≤ заданного» на 1000 случайных наборах. Режим Ask: файлы не менял, запись в `CHANGELOG_AI.md` появится вместе с реализацией.

## Ты прав наполовину

Привязка **риска** к депозиту в формуле есть: `risk_amount = balance_free * risk_per_trade` (docs/04_RISK.md:9). На лот считается только знаменатель. Но звено действительно выпало, и не одно. Каждый из пяти пробелов закрывается своим членом новой формулы:

1. **Не та база.** `balance_free` — это свободные наличные, а не капитал. После каждой покупки база уменьшается. Шорт её, наоборот, раздувает: `Portfolio` прибавляет выручку от шорта к балансу (`trading_bot/portfolio.py:289-292`), и следующая сделка получает бо́льший бюджет.
2. **Размер позиции не ограничен долей депозита.** Это и есть твоё звено. Формула ограничивает убыток на стопе, но не сумму в одной бумаге. При узком стопе она просит позицию больше депозита. Документ велит урезать лоты (docs/04_RISK.md:15-16), а код отказывает в сделке целиком (`risk_manager.py:339-353`, `:397-404`).
3. **Комиссия и проскальзывание не входят в формулу.** При `FEE_RATE=0.003` это больше половины убытка на стопе. Проверочный тест этого не видит: он пересчитывает ту же формулу без издержек (`tests/test_risk_manager.py:194-197`), то есть проверяет сам себя.
4. **В формуле не тот стоп.** Во флэте стоп ставится за экстремумом свечи (docs/03_STRATEGY.md:46), а размер всегда считается от 1.5×ATR (`risk_manager.py:372-379`). В `Signal` цены стопа нет вообще (`strategy.py:88-97`).
5. **Нет связи с дневным лимитом.** 7 позиций × 1% дают до 7% убытка за день при лимите 3%. Проверка №5 лишь запрещает новые сделки, когда лимит уже пробит.

## Формула

Обозначения:
- `P` — ожидаемая цена входа (цена лимитной заявки или лучшая цена в стакане).
- `S` — цена стопа, которая **реально уйдёт в заявку**: по ATR или за экстремумом, кратная `min_price_increment`, округлённая в сторону от входа.
- `n` — лотность, `f = FEE_RATE`, `s = SLIPPAGE_ASSUMED`.
- Всё в `Decimal` и в рублях. Инструмент не в RUB — отказ.

```
[1] База — капитал «на стопах»: сколько останется, если все открытые стопы сработают сейчас
    B = cash + Σ_long u·min(S_i, M_i)·(1−s)(1−f) − Σ_short u·max(S_i, M_i)·(1+s)(1+f)
    u — бумаг в позиции, M — текущая цена; есть открытая позиция без стопа → новых сделок нет

[5] Бюджет риска сделки
    R = min( RISK_PER_TRADE · B,
             B − (1 − DAILY_LOSS_LIMIT)     · E_day0,    # даже если сработают все стопы, день ≥ −3%
             B − (1 − GLOBAL_DRAWDOWN_STOP) · E_start )  # и общая просадка ≥ −15%
    R ≤ 0 → отказ

[3][4] Убыток на лот при срабатывании стопа (комиссия и проскальзывание на входе и на выходе)
    long:  ℓ = n · [ P·(1+s)(1+f) − S·(1−s)(1−f) ]
    short: ℓ = n · [ S·(1+s)(1+f) − P·(1−s)(1−f) ]
    ℓ ≤ 0 → отказ (стоп не с той стороны)

Потолки в лотах
    N_risk  = R / ℓ
    N_cap   = MAX_POSITION_PCT · B / (n·P)                   # [2] доля депозита в одной бумаге
    N_funds = (F − MIN_FREE_BALANCE) / (n·P·(1+s)(1+f)),  F = E − Σ|стоимость позиций|
    N_liq   = MAX_VOLUME_PARTICIPATION · средний объём 5m-свечи (в лотах)

lots = ROUND_DOWN( k · min(N_risk, N_cap, N_funds, N_liq) ),  k = VOLATILE_SIZE_SCALER в volatile, иначе 1
lots = 0 → INSUFFICIENT_SIZE; в журнал решений пишется, какой потолок сработал
```

Почему так:
- **[1]** Покупки не уменьшают базу, шорты её не раздувают. Нереализованная прибыль учитывается только в той части, которую защищает стоп.
- **[5]** Дневной лимит и общая просадка перестают быть запретом «после факта» и становятся настоящей верхней границей убытка (если не будет гэпа). Член считает худший случай «сработали все стопы сразу», поэтому отдельная поправка на корреляцию не нужна.
- **[3]** Расчёт повторяет модель `PaperBroker`: там комиссия берётся с цены, уже включающей проскальзывание (`broker/paper.py:79-81`). Поэтому инвариант можно проверять строго, без «плюс допуск».
- **`F`** — свободные деньги без плеча, шорт обеспечен на 100%. В live — не больше того, что брокер показывает доступным. Маржинальные показатели пока не подтверждены (VERIFY, docs/08_TINVEST_API.md:33).
- **Вход частями 50/30/20.** Бюджет `R` фиксируется на первой части. Каждая следующая часть = `min(доля·план, (R − риск открытой части по текущему стопу)/ℓ_нов, остатки потолков)`, где `ℓ_нов` считается от новой цены входа. Иначе докупка выше входа незаметно добавит риск.

Сознательно не взял формулы Келли и optimal-f. Им нужна надёжная оценка преимущества стратегии, а её не будет, пока не накопится статистика paper-режима. Ошибка в такой оценке ведёт к завышенному размеру позиции.

## Пример: старая и новая формулы

Депозит 100 000 ₽, позиций нет. `P=250`, `n=10`, ATR(15m) = 1 ₽ (0.4% цены), стоп 1.5×ATR, то есть `S=248.5`.

| | Старая | Новая |
|---|---|---|
| Убыток на лот в расчёте | 15 ₽ (только стоп) | 32.45 ₽ (стоп 15 + издержки 17.45) |
| Расчёт | 66 лотов = 165 000 ₽ (165% депозита) | min(30 по риску; **6 по доле**; 37 по деньгам) |
| Итог | код — отказ; по документу — 39 лотов = 97.5% депозита | 6 лотов = 15 000 ₽ |
| Реальный убыток на стопе | 1 265 ₽ = 1.27% (при 66 лотах — 2.14% вместо «1%») | 195 ₽ = 0.19% |

Что из этого следует:
- **При внутридневных стопах почти всегда будет срабатывать потолок доли депозита, а не 1% риска.** Чтобы рисковать 1% при стопе с издержками 1.3% цены, нужна позиция на 77% депозита.
  - Потолок по риску срабатывает раньше доли, только если стоп с издержками ≥ `RISK_PER_TRADE / MAX_POSITION_PCT` = 6.7% цены. При `MAX_ATR_RATIO=5%` такого почти не бывает.
  - Поэтому 1% — это верхняя граница, а не цель. Реальный риск сделки будет примерно 0.15–0.55%.
- **Попутно, и это важно: когда издержки в формуле, видна экономика стратегии.**
  - В примере тейк 2×ATR даёт +0.24 ₽ на бумагу после издержек, а стоп — −3.24 ₽.
  - Чтобы выйти в ноль, нужно ≈93% прибыльных сделок; с подпиской (0.05%) — ≈57%. Считал в упрощении «весь объём закрывается по тейку».
  - Формулой размера это не лечится. Это вопрос тарифа или таймфрейма стопа, и его стоит решить до M8.

## Тесты, которые это докажут

1. **Главное свойство: 1000 примеров hypothesis по независимому пути.** `PaperBroker` открывает `lots` по `P` и закрывает по `S`, `Portfolio` записывает сделку. Проверка: `−pnl_net ≤ R ≤ RISK_PER_TRADE·B`, без допуска. Это строже, чем docs/11_TESTING.md:11, и соответствует AGENTS.md §6.
2. **Golden-тест на 5 тиках, посчитанных вручную.** На каждом тике срабатывает свой потолок: риск, доля, деньги, ликвидность, остаток дневного лимита. Туда же — шорт и стоп за экстремумом.
3. **Отдельные тесты:**
   - шорт не увеличивает `B`;
   - открытая позиция без стопа — отказ;
   - −2.9% за день и 0.2% открытого риска — отказ;
   - докупка частями никогда не поднимает общий риск выше `R`.

## Что нужно до внедрения

- **Новые параметры в docs/07_CONFIG_SPEC.md:**
  - `MAX_POSITION_PCT=0.15` — это ≈1/7: семь позиций занимают примерно весь депозит без плеча, седьмую урежет `N_funds`. Гэп 20% против одной позиции стоит ≤3%, то есть не больше дневного лимита.
  - `MAX_VOLUME_PARTICIPATION=0.05` — стартовое значение, его нужно подобрать по фактическому проскальзыванию в журнале.
  - `ATR_STOP_MULT` уходит из формулы размера в правило цены стопа.
- **Пункты TODO(VERIFY):**
  - в чём измеряется `Candle.volume` — в лотах или бумагах (`api_client.py:125`, там просто `int`);
  - маржа и свободные средства в live (docs/08_TINVEST_API.md:33).
- **Дефект учёта, который исказит `B`.** `Portfolio.open_position` считает `notional = price * lots` без лотности (`portfolio.py:288`), а `PaperBroker` — от `lots × lot_size`. Что такое `price` в `Portfolio` (за бумагу или за лот, с проскальзыванием или без), нигде не зафиксировано. Главный тест это поймает.
- **Интерфейсы:**
  - `AccountState` должен содержать наличные, открытые позиции со стопами и текущими ценами, а также `E_day0` и `E_start` (это капитал, а не наличные).
  - Стоп считает `risk_manager` по правилу (ATR или экстремум + шаг цены). Тогда для размера и для заявки используется одна и та же цена `S`.
  - Аргументов больше пяти, поэтому вход функции — dataclass (AGENTS.md §5).

## Нужно твоё решение

1. **Подтвердить замену формулы.** Решение от 2026-10-14 (CHANGELOG_AI.md:682) закрепляло формулу из docs/04_RISK.md, поэтому замену нужно записать в CHANGELOG. Учти: для второй и следующих позиций бюджет может стать **больше**, чем сейчас, потому что старая формула считала от остатка наличных. По смыслу это правильно — 1% капитала есть 1% капитала. Но это увеличение риска, а по AGENTS.md §4.2 оно возможно только по решению человека.
2. **Выбрать `MAX_POSITION_PCT`:**
   - **0.15** — рекомендую; в примере 6 лотов;
   - **0.20** — в примере 8 лотов, гэп 15% против позиции стоит 3%.

Для внедрения нужен режим Code: правки docs/04_RISK.md, 00_PLAN.md §7, 07_CONFIG_SPEC.md, `risk_manager.py`, тестов и запись в CHANGELOG.