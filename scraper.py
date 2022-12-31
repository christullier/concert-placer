from urllib.request import urlopen
from dotenv import load_dotenv
from special_char import special_char
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
        self.venue = attribute['venue-name']
        self.city = attribute['formatted-address']
        
        self.address = self.get_address()
        self.distance = self.get_travel_distance()


    def get_address(self):
        # replace spaces with a plus for the url
        name = self.venue.replace(" ", "+")
        city = self.city.replace(" ", "+")
        api = f'https://maps.googleapis.com/maps/api/geocode/json?address={name},{city},+MI&key={API_KEY}'

        try:
            page = urlopen(api)
        except UnicodeEncodeError:
            print("OOPS")
            exit()

        html_bytes = page.read()
        
        response_string = html_bytes.decode("utf-8")
        maps_json = json.loads(response_string)

        # return maps_json['results'][0]['plus_code']['global_code'] # just in case
        return maps_json['results'][0]['formatted_address']
    
    def get_travel_distance(self):
        start = os.getenv('START_LOC')
        start = start.replace(" ", "+")
        venue = self.address.replace(" ", "+")
        api = f'https://maps.googleapis.com/maps/api/directions/json?departure_time=now&destination={venue}&origin={start}&key={API_KEY}'

        # print(api)
 
        # this is getting a ascii-incompatible character from a german city, throwing error
        # tool to find special characters and convert them to ascii-safe versions?
        try: 
            page = urlopen(api)
        except UnicodeEncodeError as UEE:
            global COUNTER # declaring here because it *modifies* the global var
            # grab part of error that has the character encoded in base 16
            message = str(UEE).split()
            base_16 = message[5]
            index = int(message[8][0:-1])
            # remove quotes and x from beginning
            base_16 = base_16[3:-1]
            ascii_value = int(base_16, 16)
            char = chr(ascii_value)
            out = special_char(char)

            # print(f"{UEE=}")
            # print(f"{index=}")
            # print(f"{ascii_value=}")
            # print(f"{char=}")
            # print(f"{out=}")
            
            if (out != None) and (MAX_TRIES > COUNTER):
                COUNTER += 1
                self.address = self.address.replace(char, out)
                self.distance = self.get_travel_distance()
            return
        except IndexError as IE:
            print("This is probably not ")
        
        html_bytes = page.read()
        
        response_string = html_bytes.decode("utf-8")
        travel_json = json.loads(response_string)
        if travel_json['status'] != "OK":
            print("\nNAVIGATION ERROR ")
            print(F"LOCATION: {self.address}")
            print(travel_json['status'])
        
        else: # all is working
            length_meters = travel_json['routes'][0]['legs'][0]['distance']['value']
            # travel time in hours:
            # length_seconds = travel_json['routes'][0]['legs'][0]['duration']['value']
            # return round(int(length_seconds)/3600, 2)
            return round(int(length_meters)/1609, 2)

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
    
    print(c1.address)
    print(c1.distance)
    print()

