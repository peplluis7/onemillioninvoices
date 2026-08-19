# Path-enabled clearing as an agentic research agenda

The repository implements deterministic clearing policies. It does **not** claim a
decentralized multi-agent prototype. The paper nevertheless supplies a useful economic
kernel and experimental baseline for a modern agentic implementation.

## Why the path primitive, not CDG, is the implementation candidate

Causal daily greedy scheduling (CDG) is a centralized benchmark. Once per day it sees the
platform-wide active graph, ranks feasible candidates, executes to a fixed point, and
carries residual records forward. A decentralized system need not reproduce that global
loop.

The local path move has bounded coordination scope. An intermediary can identify
`A -> B -> C` from two incident invoice relationships. The proposed amount can be
verified from committed source fragments on those two edges, and the authorization
coalition contains at most three firms. A length-`k` cycle needs closure discovery,
evidence for `k` relationships, and potentially `k` simultaneous authorizations. This is a
formal locality and coalition-size property, not yet evidence of lower total communication
cost, higher acceptance, privacy, or welfare.

## Mapping to the modern agentic stack

The future implementation should combine modern protocols without assigning monetary
state transitions to a language model.

- **Domain agents.** The autonomous-supply-chain methodology of Xu et al. (2024)
  provides the organizational pattern: specialized firm agents act under local objectives
  and controls. This project adds source-record financial state and auditable clearing
  actions.
- **Agent-to-agent communication.** A2A can advertise clearing capabilities, transport a
  proposal as a long-running task, and communicate status changes while counterparties
  remain internally opaque.
- **Tool and data access.** MCP can expose an invoice vault, policy service, sanction
  screen, and deterministic common-day verifier under least-privilege authorization.
- **Mandates and evidence.** AP2 provides a useful precedent for separating agent
  interaction from signed authorization, receipts, and dispute evidence. Invoice clearing
  would need domain-specific commitment, acceleration, discharge, and reversal objects.
- **Negotiation and evaluation.** AgenticPay shows how private-constraint negotiation can
  be evaluated through feasibility, efficiency, welfare, timeouts, and violations. A
  clearing benchmark should add PMR, PMR-days, acceleration, exposure, and relationship
  disclosure.

The accounting kernel must remain deterministic. Language models may interpret policies,
explain proposals, discover counterparties, or negotiate compensation. They must not
calculate authoritative capacity, consume invoice records, waive legal constraints, or
certify conservation.

## Candidate event-driven protocol

A research prototype should implement six auditable stages:

1. **Capability advertisement.** A firm publishes supported clearing modes and broad
   policy ranges without exposing its invoice book.
2. **Local discovery.** An intermediary or federated broker identifies a two-edge path.
3. **Commitment and reservation.** Each edge owner returns signed source-fragment
   commitments with a short expiry.
4. **Negotiation.** Agents negotiate discretionary terms such as acceleration,
   compensation, exposure, and discharge mode. The amount remains bounded by exact
   common-day capacity.
5. **Deterministic verification and atomic commit.** A verifier checks signatures, record
   activity, residual sufficiency, policies, and legal eligibility. Either both fragments are
   consumed and the instruction is created, or no state changes.
6. **Receipt and recovery.** Parties receive replayable receipts supporting reconciliation,
   timeout, reversal, and audit.

## Experimental design

Run four mechanisms over the same invoice-event stream:

1. centralized path CDG;
2. asynchronous path agents;
3. asynchronous cycle agents;
4. a hybrid agent market able to propose either move.

Vary participation, message delay, reservation lifetime, rejection, acceleration caps,
exposure limits, strategic withholding, identity and privacy overhead, and failure
recovery. Measure accepted PMR, normalized PMR-days, messages per accepted euro,
disclosed relationships, stale reservations, time to finality, acceleration, concentration,
fairness, and distance from tractable full-information bounds.

CDG measures what the move set can achieve before communication and consent frictions.
The tractable LP is the oracle reference. The agent-to-CDG gap estimates coordination and
participation losses; the CDG-to-LP gap estimates centralized heuristic and foresight
losses.

The primary hypothesis is that fixed three-firm paths retain a larger share of centralized
PMR than longer cycles when participation falls or consent becomes unreliable. Secondary
hypotheses concern fewer stale reservations, smaller disclosure scope, and faster finality.
These may fail if repeated local search, strategic behavior, adverse selection, or mandate
overhead outweigh coalition-size benefits. Testing that trade-off is the research agenda.

## Selected modern references

- Guo et al. (2024), *Large Language Model Based Multi-Agents: A Survey of Progress and
  Challenges*, IJCAI, DOI 10.24963/ijcai.2024/890.
- Xu et al. (2024), *On Implementing Autonomous Supply Chains: A Multi-Agent System
  Approach*, Computers in Industry, DOI 10.1016/j.compind.2024.104120.
- Agent2Agent Protocol Project, official A2A specification and repository:
  https://github.com/a2aproject/A2A
- Model Context Protocol, official specification:
  https://modelcontextprotocol.io/specification/2025-11-25
- Agent Payments Protocol, official v0.2 specification:
  https://ap2-protocol.org/ap2/specification/
- Liu, Gu, and Song (2026), *AgenticPay*, DOI 10.48550/arXiv.2602.06008.
