import geopandas

county_streams = geopandas.read_file("https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24001_linearwater.zip")
print(county_streams.info())

area_water = geopandas.read_file("https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24031_areawater.zip")
print(area_water.info())

