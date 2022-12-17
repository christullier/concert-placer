from urllib.request import urlopen
import json

class Concert():
    def __init__(self, artist, is_sold_out, start_date, end_date, venue, address):
        self.artist = artist
        self.is_sold_out = is_sold_out
        self.start_date = start_date
        self.end_date = end_date
        self.venue = venue
        self.address = address

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
    tour_string = html_bytes.decode("utf-8")
    tour_json = json.loads(tour_string)

    return tour_json

url = "http://stephenday.org/tour"
id = get_artist_id(url)
print(id)

tour_json = get_tour_info(id)
for i in tour_json['included']:
    attribute = i['attributes']
    print(json.dumps(attribute, indent=1))
    is_sold_out = attribute['is-sold-out']
    start_date = attribute['starts-at-date-local']
    end_date = attribute['ends-at-date-local']
    venue = attribute['venue-name']
    address = attribute['formatted-address']

# separate the data
