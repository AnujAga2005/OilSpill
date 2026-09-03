import geopandas as gpd

# Read MapInfo TAB file
gdf = gpd.read_file("input.tab", encoding="utf-8")

# Save as ESRI Shapefile with UTF-8 encoding
gdf.to_file("output.shp", driver="ESRI Shapefile", encoding="utf-8")