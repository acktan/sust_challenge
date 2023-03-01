import geopandas as gpd
import glob
import pandas as pd
from tqdm import tqdm

class Data():
    def __init__(self, path: str = '../data/') -> None:
        self.path = path
    
    def get_shapefiles(self) -> dict:
        """
        Load all .shp files in folder to geopandas dataframes
        
        Args:
            path: the folder they are located in
            
        Returns:
            shapes: dict with all geopandas dataframes
        
        """
        shapefiles = glob.glob(self.path + '*.shp')
        shapes = {}
        for shapefile in tqdm(shapefiles):
            name  = shapefile.split('/')[-1].split('\\')[-1].split('.')[0]
            shapes[name] = gpd.read_file(shapefile)
            
        return shapes
    
    def calculate_road_density(self,
                               shapefiles: dict,
                               highways_only: bool = True) -> pd.DataFrame:
        """Calculate road density per region
        
        Args:
            shapefiles: dict of shapefiles loaded in previous 
            highways_only: whether you want to consider only highways or not
            
        Returns:
            df: dataframe breaking down road density for each region
        """
        routes = shapefiles['VSMAP_TOUT']
        
        if highways_only:
            routes = routes[routes.lib_rte.str.startswith('A')]
        
        regions = shapefiles['FRA_adm1']
        regions = regions.to_crs(epsg=2154)
        regions['area_m'] = regions.geometry.area

        joined = gpd.sjoin(routes, regions, predicate='within')
        joined['length_m'] = joined.geometry.length

        total_length_by_region = joined.groupby('NAME_1')['length_m'].sum()
        
        regions = pd.merge(regions, total_length_by_region, on='NAME_1', how='inner')
        #absolute mess of calculations
        temp_a = (joined.groupby(['NAME_1', 'lib_rte'])['length_m'].sum() / 2).groupby('NAME_1').max()
        temp_b = (joined.groupby(['NAME_1', 'lib_rte'])['length_m'].sum() / 2).groupby('NAME_1').mean()

        temp_c = pd.merge(temp_a, temp_b, on='NAME_1').rename(columns={'length_m_x': 'length_max',
                                                                       'length_m_y': 'length_mean'})
        
        regions = pd.merge(regions, temp_c, on='NAME_1', how='inner')
        regions['road_density'] = regions['length_m'] / regions['area_m']
        
        df = regions[['NAME_1', 'road_density', 'length_m', 'area_m', 'length_max', 'length_mean']].sort_values(by='road_density', ascending=False)
        
        return df
    
    def create_df(self,
                  highways_only: bool = False) -> pd.DataFrame:
        """Create the initial dataframe and calculate road density

        Args:
            highways_only (bool, optional): _description_. Defaults to False.

        Returns:
            pd.DataFrame: _description_
        """
        shapefiles = self.get_shapefiles()
        df = self.calculate_road_density(shapefiles, highways_only = highways_only)
        
        return df
