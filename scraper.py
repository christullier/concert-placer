from urllib.request import urlopen
from dotenv import load_dotenv
import json
import os

home = 'Alexandria, Virginia'

load_dotenv()
API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

class Concert():
    def __init__(self, artist_id, attribute):
        self.artist_id = artist_id
        self.is_sold_out = attribute['is-sold-out']
        self.start_date = attribute['starts-at-date-local']
        self.end_date = attribute['ends-at-date-local']
        self.venue = attribute['venue-name']
        self.city = attribute['formatted-address']
        
        self.address = self.get_address()
        self.travel_time = self.get_travel_time()

        # make distance function to get distance from starting address
        # self.distance = X
        # self.address as well

    def get_address(self):
        # replace spaces with a plus for the url
        name = self.venue.replace(" ", "+")
        city = self.city.replace(" ", "+")
        api = f"https://maps.googleapis.com/maps/api/geocode/json?address={name},{city},+MI&key={API_KEY}"
        page = urlopen(api)
        html_bytes = page.read()
        
        response_string = html_bytes.decode("utf-8")
        maps_json = json.loads(response_string)

        # return maps_json['results'][0]['plus_code']['global_code'] # just in case
        return maps_json['results'][0]['formatted_address']
    
    def get_travel_time(self):
        start = os.getenv('START_LOC')
        start = start.replace(" ", "+")
        venue = self.address.replace(" ", "+")
        api = f"https://maps.googleapis.com/maps/api/directions/json?departure_time=now&destination={venue}&origin={start}&key={API_KEY}"

        print(api)
        
        page = urlopen(api)
        html_bytes = page.read()
        
        response_string = html_bytes.decode("utf-8")
        travel_json = json.loads(response_string)
        length_seconds = travel_json['routes'][0]['legs'][0]['duration']['value']
        print(str(round(int(length_seconds)/3600, 2)) + " hours")
        return round(int(length_seconds)/3600, 2)
        # with open('travel.json', 'w') as f:
        #     f.write(response_string)


def get_artist_id(url):
    page = urlopen(url)
    html_bytes = page.read()
    html = html_bytes.decode("utf-8")

    # find 'artist-id'
    # 11 to account for the name and the open quote
    start = html.find('artist-id=') + 11 
    end = start + 36

    artist_id = html[start:end]
    return artist_id

def get_tour_info(artist_id):
    api = f"https://cdn.seated.com/api/tour/{artist_id}?include=tour-events"

    page = urlopen(api)
    html_bytes = page.read()
    response_string = html_bytes.decode("utf-8")
    tour_json = json.loads(response_string)

    return tour_json

    
url = "http://stephenday.org/tour"
id = get_artist_id(url)

tour_json = get_tour_info(id)
for i in tour_json['included']:
    attribute = i['attributes']
    c1 = Concert(id, attribute)
    
    print(c1.address)
    print(c1.travel_time)
    exit()

