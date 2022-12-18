from urllib.request import urlopen
from dotenv import load_dotenv
import json
import os


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

        # make distance function to get distance from starting address
        # self.distance = X


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

def get_address(name, city):
    # replace spaces with a plus
    name = name.replace(" ", "+")
    city = city.replace(" ", "+")
    api = f"https://maps.googleapis.com/maps/api/geocode/json?address={name},{city},+MI&key={API_KEY}"
    page = urlopen(api)
    html_bytes = page.read()
    response_string = html_bytes.decode("utf-8")
    maps_json = json.loads(response_string)
    print(maps_json['results'][0]['formatted_address'])
    

url = "http://stephenday.org/tour"
id = get_artist_id(url)
print(id)

tour_json = get_tour_info(id)
for i in tour_json['included']:
    attribute = i['attributes']
    print(json.dumps(attribute, indent=1))

    c1 = Concert(id, attribute)
    print(c1.city)
    print(c1.venue)
    get_address(c1.venue, c1.city)

    exit()

# separate the data
