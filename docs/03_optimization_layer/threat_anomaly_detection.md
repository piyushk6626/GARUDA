# Threat Anomaly Detection

Tool: `detect_threat_anomalies`

## Problem
Flag unusual activity in tracks and emitter/sensor events: sudden SAM relocation, unusual CAP density near a corridor, emitter activation patterns, deviating civil tracks, so planners are alerted before they are formally reported.

## Data
`threat_track`, synthetic emitter events (`ts, emitter_id, freq_band, prf, location, confidence`), ADS-B tracks (real). Synthetic anomalies injected with labels for evaluation (e.g. 2% of windows).

## Methods
1. **Isolation Forest** on window features: per-grid-cell counts of tracks/emissions per hour, mean speed, heading variance, distance to border, deviation from historical hourly baseline. Contamination ~0.02.
2. **DBSCAN / HDBSCAN** spatial clustering of detections to find new clusters (potential new SAM batteries) not matching known threats (distance > eps from known sites).
3. **Statistical process control**: z-score / CUSUM on counts per cell for rate changes.
4. **Track kinematic anomaly**: Kalman filter innovation (Mahalanobis distance) for track jumps; speed above type envelope.
Ensemble: score = max-normalised combination; alert if ≥ 2 detectors agree or score > high threshold.

## Pseudocode
```
windows = rolling_windows(events, 30min)
F = features(windows)
iso_score = IsolationForest.fit(F_baseline).score_samples(F)
clusters = HDBSCAN(min_cluster_size=3).fit(positions_new)
new_clusters = clusters not within 5km of known threats
cusum alerts per cell
alerts = fuse(iso_score, new_clusters, cusum)
```

## Tool signature
```python
class AnomalyInput(BaseModel):
    area: Polygon | None = None
    lookback_hours: int = 6
    sensitivity: Literal["low","medium","high"] = "medium"

class AnomalyOutput(BaseModel):
    alerts: list[AnomalyAlert]   # id, type, location, score, evidence, suggested_threat_update
    baseline_period: str
```
Alerts may trigger `retask_rolling_horizon` with `new_sam` event after analyst/human confirmation.

## Validation
Precision/recall on injected anomalies, false alarm rate per day, ROC; analyst-in-the-loop confirm.

## Failure modes
Alert fatigue -> thresholds per sensitivity, dedupe within 10 km / 1 h.
