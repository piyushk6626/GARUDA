# GIS, Terrain, and Bases

## Purpose
Static geography: borders, terrain elevation, airfields, roads and cities, used for route cost surfaces, distances, and masking.

## Real sources
| Data | Source | Format |
|---|---|---|
| Admin boundaries, borders | Natural Earth 1:10m | Shapefile/GeoJSON |
| Roads, cities, water, airfields | OpenStreetMap via Geofabrik India extract or Overpass | PBF/GeoJSON |
| Terrain elevation | SRTM 30m (NASA/USGS) or Copernicus DEM | GeoTIFF |
| Airfields / runways | OurAirports `airports.csv`, `runways.csv` | CSV |

## Entities
- `base`: `base_id, name, icao, position, elevation_ft, runway_count, runway_length_m, shelter_count, fuel_capacity_l, ground_crew_teams, max_sorties_per_hour`. Runway data real (OurAirports); capacities synthetic.
- `terrain_grid`: DEM resampled to a 1 km grid over the theatre bbox with `elev_m, slope`.
- `geo_feature`: OSM roads/cities/rivers (for mobile threat movement and target placement).
- Distance matrix `base_to_target_km` precomputed (great-circle via `pyproj.Geod`).

## Processing
1. Download and clip to bbox (e.g. lat 22-33, lon 68-78).
2. Resample DEM with `rasterio` to grid (1 km); compute terrain-masking proxy = elevation above ground level for low-level routes.
3. Load vector data with `osm2pgsql` or `ogr2ogr` into PostGIS.

## Storage
- PostGIS: `base`, `geo_feature`, `border` (geometry, GiST index).
- MinIO: `raw/srtm/*.tif`, `processed/dem_1km.tif`, `processed/cost_layers/`.
- Redis: `base:{id}` hash for quick lookup; grid arrays loaded in memory by the optimization service at startup (`numpy` memmap).
- Neo4j: `(:Base)` nodes.

## Tools
`spatial_query`, `distance_matrix`, route planning cost layers.
