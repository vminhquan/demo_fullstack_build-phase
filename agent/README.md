# Scenario Forge Agent

Service sinh kịch bản: prompt + dữ liệu CARLA (catalog.v1) → ScenarioIR → `.xosc` OpenSCENARIO 1.0 đặt trên làn thật của map. Chỉ Backend gọi service này (header `X-API-Key`); Agent không có database.

```text
app/
├── main.py                 # FastAPI: GET /health, POST /v1/scenarios/generate
├── config.py, llm.py       # settings (OPENAI_API_KEY, MODEL_NAME, AGENT_API_KEY, XOSC_FLIP_Y…), ChatOpenAI
├── contracts/              # catalog_v1.py (contract chung với Backend/Worker), api.py
└── scenario/
    ├── graph.py            # LangGraph: intent → 6 bước sinh → guardrail ↔ Z3 → grounding + xosc
    ├── generator.py        # 6 bước (LLM hoặc quy tắc offline), có ngữ cảnh map
    ├── grounding.py        # chọn spawn point ego, đặt actor lên làn thật, chọn blueprint
    ├── xosc.py             # xuất OpenSCENARIO + kiểm tra XSD
    ├── rag.py              # Qdrant in-memory + BM25, nạp sẵn từ knowledge/
    └── validator.py, smt.py, curve_dynamics.py, threat.py, schemas.py, state.py
knowledge/                  # regulations.json, seed_scenarios.json, project_docs/, schemas/OpenSCENARIO_1_0.xsd
```

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # offline, không gọi OpenAI
OPENAI_API_KEY=sk-... .venv/bin/uvicorn app.main:app --port 8100
```

Thiết kế, contract và các điểm chưa kiểm chứng: [../docs/19-agent-sinh-kich-ban.md](../docs/19-agent-sinh-kich-ban.md).
