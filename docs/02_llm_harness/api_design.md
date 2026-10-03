# API Design (FastAPI)

## Structure
```
app/main.py            # create_app(), lifespan
app/api/deps.py        # dependency providers (db pools, graph, llm)
app/api/routers/{chat,plans,data,scenario,health}_routes.py
app/schemas/           # request/response models
```

## Lifespan
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg = await asyncpg.create_pool(settings.pg_dsn)
    app.state.neo = AsyncGraphDatabase.driver(settings.neo_uri, auth=...)
    app.state.redis = redis.asyncio.from_url(settings.redis_url)
    app.state.agent = await build_graph(checkpointer=await make_checkpointer(app.state.pg))
    yield
    await app.state.pg.close(); await app.state.neo.close(); await app.state.redis.aclose()
```

## Endpoints
| Method | Path | Description |
|---|---|---|
| POST | `/v1/chat` | Send message; returns `session_id` and final response |
| GET | `/v1/chat/{session_id}/stream` | SSE: tokens, tool_call, tool_result, plan_ready, awaiting_approval |
| GET | `/v1/plans/{plan_id}` | Plan with assignments, KPIs, explanations |
| POST | `/v1/plans/{plan_id}/approve` | Body `{comment}`; resumes graph |
| POST | `/v1/plans/{plan_id}/reject` | Body `{comment}` |
| GET | `/v1/plans/{plan_id}/diff?against=` | Change vs. previous plan |
| GET | `/v1/data/aircraft`, `/crew`, `/threats`, `/airspace`, `/weather`, `/missions` | Direct data endpoints for UI |
| POST | `/v1/tools/{tool_name}` | Call a single tool directly (testing, RBAC protected) |
| POST | `/v1/scenario/inject-event` | Trigger scripted event |
| POST | `/v1/scenario/clock` | Set speed/pause/reset |
| GET | `/v1/events/stream` | SSE of live operational events |
| GET | `/healthz` | Liveness and dependency checks |

## Example models
```python
class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str

class ApprovalRequest(BaseModel):
    comment: str | None = None

class InjectEventRequest(BaseModel):
    type: Literal["aircraft_nmc","new_sam","base_closed","p1_request","tanker_lost","weather_override"]
    params: dict[str, Any] = {}
    at_scenario_time: datetime | None = None
```

## Cross-cutting
- Auth: JWT with roles (planner, commander, observer); API key for prototype.
- Middleware: request ID, structured logging (structlog), exception handler to uniform error JSON, timing metrics (Prometheus).
- CPU-bound solvers run in a `ProcessPoolExecutor` (or Celery/Arq workers) so the event loop is not blocked; job status endpoint `GET /v1/jobs/{id}` for long solves.
- Return type annotations and `response_model` on every route.
- Rate limit chat per user.
