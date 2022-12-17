from urllib.request import urlopen

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
    json = html_bytes.decode("utf-8")

    return json

url = "http://stephenday.org/tour"
id = get_artist_id(url)
print(id)

tour_json = get_tour_info(id)
print(tour_json)
