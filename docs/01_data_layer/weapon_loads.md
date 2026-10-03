# Weapon Loads

## Purpose
What munitions exist, where, and which aircraft can carry which configurations against which targets.

## Source
Synthetic with **generic, fictional** specs (do not use real classified performance).

## Entities
1. **WeaponType** catalog: `weapon_code, category (AAM_BVR, AAM_WVR, ASM, LGB, UNGUIDED, ANTI_RADIATION, ANTI_SHIP, PODS), range_km, weight_kg, pk_by_target_class (jsonb), min_release_alt_ft, guidance`.
2. **WeaponStock**: `base_id, weapon_code, quantity, reserved_qty, updated_at`.
3. **LoadConfig**: `config_id, aircraft_type, stations (jsonb: station -> weapon_code x count), role (DCA/STRIKE/SEAD/CAS/ISR), total_weight_kg, range_penalty_pct`.
4. **Compatibility**: `aircraft_type, weapon_code, allowed_stations[]`.

## Generation rules
- ~14 weapon types; Pk per (weapon, target class) in [0.3, 0.9] with hardness penalties.
- Stock per base ~ LogNormal, 200-1500 for common, 20-100 for precision/standoff.
- 3-6 load configs per aircraft type; weight must not exceed station/MTOW limits (validation).
- Consumption: simulator decrements `quantity` on mission completion; resupply events add stock.

## Storage
```sql
CREATE TABLE weapon_type (weapon_code text PRIMARY KEY, category text, range_km real,
  weight_kg real, pk_by_target_class jsonb);
CREATE TABLE weapon_stock (base_id text, weapon_code text, quantity int CHECK (quantity>=0),
  reserved_qty int DEFAULT 0, PRIMARY KEY (base_id, weapon_code));
CREATE TABLE load_config (config_id text PRIMARY KEY, aircraft_type text, role text,
  stations jsonb, total_weight_kg real, range_penalty_pct real);
```
Neo4j: `(:WeaponStock)-[:STORED_AT]->(:Base)`, `[:OF_TYPE]->(:WeaponType)`. Redis: `stock:{base}:{weapon}` counters (atomic DECRBY on reservation). Kafka: `garuda.weapons.stock`.

## Sample
```json
{"config_id":"SU30-STRIKE-1","aircraft_type":"Su-30MKI","role":"STRIKE","stations":{"S3":["LGB-500x2"],"S5":["ASM-STANDOFFx1"],"S1":["AAM-WVRx1"],"S12":["AAM-WVRx1"]}}
```

## Consumers
MILP assignment, weapon-target assignment, sortie generation (munitions constraint).
