import requests


URLS = ['https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001',
        'https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001']


_sitemaps = {}


def _load_sitemaps(key):
    sitemaps = {}
    
    params = {'Authorization': key}

    for url in URLS:
        r = requests.get(url, params=params)
        for s in r.json()['records']['Station']:
            #sitemaps[s['StationName']] = url
            c = (float(s['GeoInfo']['Coordinates'][1]['StationLatitude']),
                 float(s['GeoInfo']['Coordinates'][1]['StationLongitude']))
            sitemaps[s['StationName']] = {'url': url, 'coor': c}
    print('sitemaps built')

    return sitemaps

def cwa2(site: str | tuple, key: str) -> dict:
    '''weather info grabber grom CWA
    site   - site name or coordinates
    key    - CWA key
    return - weather info in dict
    '''
    global _sitemaps

    if not _sitemaps:
        _sitemaps = _load_sitemaps(key)

    if type(site) == tuple:
        site = _nearest(site)    

    url = _sitemaps.get(site, {}).get('url')
    if url:
        return _cwa(url, site, key)
    return {}

def _nearest(site):
    return min(((site[0] - v['coor'][0]) ** 2 + (site[1] - v['coor'][1]) ** 2, s) for s, v in _sitemaps.items())[1]

def cwa(site, key):
    # info = _cwa(URL[0], site, key)
    # if info:
    #     return info
    # return _cwa(URL[1], site, key)

    #return _cwa(URL[0], site, key) or _cwa(URL[1], site, key)

    for url in URLS:
        info = _cwa(url, site, key)
        if info:
            return info
    return {}

def _cwa(url, site, key):
    params = {'Authorization': key,
              'StationName': site
             }
    try:
        r = requests.get(url, params=params)
    except Exception as e:
        print(e)
        return {}

    if r.status_code != 200:
        print(r.text)
        return {}

    if not r.json()['records']['Station']:
        return {}

    raw = r.json()['records']['Station'][0]
    s = raw['StationName']
    o = raw['ObsTime']['DateTime'].replace('+08:00', '')
    c = (float(raw['GeoInfo']['Coordinates'][1]['StationLatitude']),
         float(raw['GeoInfo']['Coordinates'][1]['StationLongitude']))
    _r = float(raw['WeatherElement']['Now']['Precipitation'])
    t = float(raw['WeatherElement']['AirTemperature'])
    h = float(raw['WeatherElement']['RelativeHumidity']) / 100
    
    return {'S': s, 'O': o, 'C': c, 'R': _r, 'T': t, 'H': h}

def tostr(info_dict, sep=', '):
    buf = []
    if 'S' in info_dict:
        buf.append(f"測站: {info_dict['S']}")
    if 'C' in info_dict:
        buf.append(f"座標: {info_dict['C']}")
    if 'O' in info_dict:
        buf.append(f"時間: {info_dict['O']}")
    if 'T' in info_dict:
        buf.append(f"溫度: {info_dict['T']}度")
    if 'H' in info_dict:
        buf.append(f"濕度: {info_dict['H']:.0%}")
    if 'R' in info_dict:
        buf.append(f"雨量: {info_dict['R']}mm")
    return sep.join(buf)

if __name__ == '__main__':
    print(cwa2('苗栗', 'YOUR_KEY'))


