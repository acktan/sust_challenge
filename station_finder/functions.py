import branca.colormap as cm
import folium
import geopandas as gpd
import numpy as np
from shapely.geometry import MultiLineString, Point, LineString
from shapely import ops
import pandas as pd
from tqdm import tqdm

import warnings
warnings.filterwarnings("ignore")


class WeightedLineString(LineString):
    """Add weight to regular shapely.LineString
    
    Args:
        weight: traffic value
    """
    def __init__(self, *args, **kwargs):
        self.weight = kwargs.pop('weight', 1.0)
        super().__init__(*args, **kwargs)
        
class WeightedMultiLineString(MultiLineString):
    """Add weight to shapely.MultiLineString
    
    Args:
        weight: traffic value
    """
    def __init__(self, lines, weights=None, **kwargs):
        if weights is None:
            weights = [1.0] * len(lines)
        self.lines = [WeightedLineString(line, weight=weight) for line, weight in zip(lines, weights)]
        super().__init__(self.lines, **kwargs)

    @property
    def weights(self):
        return [line.weight for line in self.lines]
class StationLocator():
    def __init__(self,
                 shapefiles: dict,
                 csvs: dict,
                 crs: str = '2154') -> None:
        """Create necessary datasets to calculate grid-search
        
        Args:
            shapefiles: dict containing all shapefiles loaded from Data()
            csvs: dict containting all csvs loaded from Data()
            crs: EPSG rules to follow for geometric data, default is RGF93 v1 / Lambert-93
        
        """
        # init global variables
        self.crs = crs
        
        # Loading road and traffic data
        self.data = shapefiles['TMJA2019'].set_crs(self.crs)
        self.data = self.data.explode('geometry')
        self.data['PL_traffic'] = self.data['TMJA'] * (self.data['ratio_PL']/100)
        self.data['PL_traffic'] = (self.data['PL_traffic'] - self.data['PL_traffic'].min()) / \
                                    (self.data['PL_traffic'].max() - self.data['PL_traffic'].min())
        self.road_segments = self.data.geometry
        self.traffic_only = self.data.PL_traffic

        # Loading gas station data
        self.stations = csvs['pdv'].dropna(subset=['latlng'])
        self.stations[['lat', 'long']] = self.stations['latlng'].str.split(',', expand=True).astype(float)
        self.stations['geometry'] = self.stations.apply(lambda row: Point(row['long'], row['lat']), axis=1)
        self.stations = gpd.GeoDataFrame(self.stations[['id', 'typeroute', 'services', 'geometry']]).set_crs(self.crs)
        
        # Loading production hub data
        self.air_logis = pd.concat([shapefiles['Aires_logistiques_elargies'], shapefiles['Aires_logistiques_denses']])
        self.air_logis_info = csvs['aire_loqistique'].rename(columns={'Surface totale': 'surface_totale'})
        self.air_logis_info.columns.values[0] = 'e1'
        self.air_logis = gpd.GeoDataFrame(pd.merge(self.air_logis_info[['e1', 'surface_totale']], self.air_logis, on='e1', how='inner')).set_crs(self.crs)
        self.air_logis['surface_totale'] = (self.air_logis['surface_totale'] - self.air_logis['surface_totale'].min()) / \
                                            (self.air_logis['surface_totale'].max() - self.air_logis['surface_totale'].min())
        self.air_logis['geometry'] = self.air_logis.geometry.centroid
    
    def create_network(self, 
                               road_segments: list[object], 
                               traffic_values: list[float]) -> MultiLineString:
        """Combine all geometric segments into one large MultiLineString with custom weights
        
        Args:
            road_segments: list of linestrings & multilinestrings
            traffic_values: list of traffic values for each segment
        Returns:
            network: combined linestrings into multilinestring with custom weights
        """
        segments = []
        for segment, traffic in zip(road_segments, traffic_values):
            if isinstance(segment, WeightedLineString):
                segment_with_weight = segment
            elif isinstance(segment, LineString):
                segment_with_weight = WeightedLineString(segment.coords, weight=traffic)
            elif isinstance(segment, MultiLineString):
                sub_segments_with_weight = []
                for sub_segment in segment.geoms:
                    if isinstance(sub_segment, WeightedLineString):
                        sub_segment_with_weight = sub_segment
                    elif isinstance(sub_segment, LineString):
                        sub_segment_with_weight = WeightedLineString(sub_segment.coords, weight=traffic)
                    sub_segments_with_weight.append(sub_segment_with_weight)
                segment_with_weight = WeightedMultiLineString(sub_segments_with_weight, weight=traffic)
            segments.append(segment_with_weight)
        
        network = ops.unary_union(segments)
        return network

    def score_locations(self, 
                       candidate: Point, 
                       road_network: MultiLineString,
                       gas_stations: bool = False) -> float:
        
        """Compute score for candidate location
        
        Args:
            candidate: geometric Point
            networks: road network with coordinates and weights
            gas_stations: include gas station locations into calculation
                        
        Returns:
            score: cumulative score
            
        """
        max_road = 500 # Maximum distance for traffic & roads
        max_distance = 10_000 # Maximum distance to consider for aires
        
        proximity_weight = 2 # weight for proximity score
        traffic_weight = 5 # weight for traffic score
        aires_weight = 10 # weight for the aires logistique
        station_weight = -2
        score = 0
        
        # Calculating road distance & traffic 
        for i, network in enumerate(road_network):
            distance = candidate.distance(network)

            proximity_score = 0.0
            if distance <= max_road/2:
                proximity_score = (max_road - distance) / max_road
                if isinstance(network, WeightedLineString):
                    traffic_score = network.weight
                elif isinstance(network, MultiLineString):
                    traffic_score = np.mean([line.weight for line in network])
                else:
                    traffic_score = 0
            elif distance <= max_road:
                proximity_score = (max_road - distance) / max_road / 2
                if isinstance(network, WeightedLineString):
                    traffic_score = network.weight / 2
                elif isinstance(network, MultiLineString):
                    traffic_score = np.mean([line.weight for line in network]) / 2
                else:
                    traffic_score = 0
            else:
                continue  
                       
            score += proximity_weight * proximity_score + traffic_score * traffic_weight
        
        
        # Calculating proximity to logistic centers
        for i, point in enumerate(self.air_logis.geometry):
            distance = candidate.distance(point)
            
            aires_score = 0.0
            if distance <= max_distance/2:
                aires_score = (max_distance - distance) / max_distance
                aires_score += self.air_logis.surface_totale[i]
            elif distance <= max_distance:
                aires_score = (max_distance - distance) / max_distance / 2
                aires_score += self.air_logis.surface_totale[i] / 2
            else: 
                continue
            
            score += aires_score * aires_weight

        if gas_stations:
            # Calculating proximity to existing gas stations
            for i, station in enumerate(self.stations.geometry):
                distance = candidate.distance(station)
                
                station_score = 0.0
                if distance <= max_distance/2:
                    station_score = (max_distance - distance) / max_distance
                elif distance <= max_distance:
                    station_score = (max_distance - distance) / max_distance / 2
                else:
                    continue
                
                score += station_score * station_weight    
                
        return score
    
    def get_best_location(self,
                          grid_size: int = 100_000,) -> list:
        
        """Identify top X locations on map based on pre-defined parameters
        
        Args:
            grid_size = distance between points on map, in meters
            num_locations = number of top locations to be returned
            
        Returns:
            sorted_locations: coordinates, weighted_score of top X locations
        """
        network = self.create_network(self.road_segments, self.traffic_only)

        # creating the boundary of our grid
        xmin, ymin, xmax, ymax = network.bounds
        x_coords = np.arange(xmin, xmax + grid_size, grid_size)
        y_coords = np.arange(ymin, ymax + grid_size, grid_size)
        
        # setting up the grid points
        grid_points = np.transpose([np.tile(x_coords, len(y_coords)), np.repeat(y_coords, len(x_coords))])
        candidate_locations = [Point(x, y) for x, y in grid_points]
        
        weighted_scores  = [self.score_locations(candidate, network) for candidate in tqdm(candidate_locations)]
            
        sorted_locations = sorted(zip(candidate_locations, weighted_scores), key=lambda x: x[1], reverse=True)
        
        return sorted_locations
    
    def visualize_results(self,
                          sorted_locations: list,
                          num_locations: int = 25,
                          colors: list[str] = None) -> None:
        """Visualize top locations on map
        
        Args:
            sorted_locations: list of coordinates, weighted score of locations
            colors: list of colors for traffic heatmap
            
        """
        
        france_center = [46.2276, 2.2137]
        m = folium.Map(location=france_center, zoom_start=6, tiles='cartodbpositron')

        values = np.quantile(self.data['PL_traffic'], [np.linspace(0, 1, 7)])
        values = values[0]
        if colors is None:
            colors = ['#00ae53', '#86dc76', '#daf8aa', '#ffe6a4', '#ff9a61', '#ee0028']
            
        colormap_dept = cm.StepColormap(colors=colors,
                                        vmin=min(self.data['PL_traffic']),
                                        vmax=max(self.data['PL_traffic']),
                                        index=values)

        style_function = lambda x: {'color': colormap_dept(x['properties']['PL_traffic']),
                                    'weight': 2.5,
                                    'fillOpacity': 1}
        
        roads = folium.GeoJson(self.data,
                                 name='Routes',
                                 style_function=style_function
                                )
        top_locations = folium.GeoJson(gpd.GeoDataFrame(sorted_locations[:num_locations], geometry=0).set_crs(self.crs),
                              style_function=lambda x: {'color': 'red',
                                                        'weight': 2}
                              )
        
        roads.add_to(m)
        top_locations.add_to(m)
        
        m.save('map.html')
        