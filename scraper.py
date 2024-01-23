from urllib.request import urlopen
from dotenv import load_dotenv
from special_char_replacement import special_char_replacement
import json
import os

load_dotenv()
API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')
COUNTER = 0
MAX_TRIES = 5

class Concert():
    def __init__(self, artist_id, attribute):
        self.artist_id = artist_id
        self.is_sold_out = attribute['is-sold-out']
        self.start_date = attribute['starts-at-date-local']
        self.end_date = attribute['ends-at-date-local']
        self.venue = self.ascii_fix(attribute['venue-name'])
        self.city = self.ascii_fix(attribute['formatted-address'])

        self.address = self.get_address()
        self.distance = self.get_travel_distance()

        self.is_drivable = True

    def get_address(self):
        # replace spaces with a plus for the url
        name = self.venue.replace(" ", "+")
        city = self.city.replace(" ", "+")
        api = f'https://maps.googleapis.com/maps/api/geocode/json?address={name},{city},+MI&key={API_KEY}'

        page = urlopen(api)

        html_bytes = page.read()

        response_string = html_bytes.decode("utf-8")
        maps_json = json.loads(response_string)
        address = maps_json['results'][0]['formatted_address']
        address = self.ascii_fix(address)
        # return maps_json['results'][0]['plus_code']['global_code'] # just in case
        return address

    # checks the address for valid characters so we don't have to get an error later
    def ascii_fix(self, value):
        if not value.isascii():
            for i, c in enumerate(value):
                if not c.isascii():
                    r = special_char_replacement(c)
                    value = value.replace(c, r)
        return value

    def get_travel_distance(self):
        start = os.getenv('START_LOC')
        start = start.replace(" ", "+")
        venue = self.address.replace(" ", "+")
        api = f'https://maps.googleapis.com/maps/api/directions/json?departure_time=now&destination={venue}&origin={start}&key={API_KEY}'

        page = urlopen(api)
        html_bytes = page.read()
        response_string = html_bytes.decode("utf-8")
        travel_json = json.loads(response_string)

        # is there a path?
        if travel_json['status'] != "OK":
            print("\nNAVIGATION ERROR ")
            print(F"LOCATION: {self.address}")
            print(travel_json['status'])
            self.is_drivable = False

        # all is working
        else:
            length_meters = travel_json['routes'][0]['legs'][0]['distance']['value']
            # travel time in hours:
            # length_seconds = travel_json['routes'][0]['legs'][0]['duration']['value']
            # return round(int(length_seconds)/3600, 2)
            return round(int(length_meters)/1609, 2)

    def print_info(self):
        print()
        print(self)

        if self.is_sold_out:
            print("**SOLD OUT**")
            return
        elif not self.is_drivable:
            print("**not driveable")
            return

        print(self.distance)

    def __str__(self):
        return f"{self.city}\n{self.start_date}"


def get_artist_id(url):
    page = urlopen(url)
    html_bytes = page.read()
    html = html_bytes.decode("utf-8")

    # find 'artist-id'
    # 11 to account for the name and the open quote
    start = html.find('artist-id="') + 11
    # the artist-id is 36 characters long
    end = start + 36

    artist_id = html[start:end]
    return artist_id

# have a different workflow in case the artist doesn't use Seated
# sammy rae and friends use bandsintown https://rest.bandsintown.com/artists/Sammy Rae Music/events?app_id=squarespace-blackbird-frog-4sgd&date=upcoming
def get_tour_info(artist_id):
    api = f"https://cdn.seated.com/api/tour/{artist_id}?include=tour-events"

    page = urlopen(api)
    html_bytes = page.read()
    response_string = html_bytes.decode("utf-8")
    tour_json = json.loads(response_string)

    return tour_json


url = os.getenv('ARTIST_URL')
artist_id = get_artist_id(url)

tour_json = get_tour_info(artist_id)

for i in tour_json['included']:
    attribute = i['attributes']
    c1 = Concert(artist_id, attribute)

    c1.print_info()
