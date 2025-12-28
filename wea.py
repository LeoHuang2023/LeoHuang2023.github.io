# dependencies: requests, bs4


__version__ = '0.2.5'


from threading import Thread

import requests
from bs4 import BeautifulSoup


_URLS = {'site_map': 'https://www.cwa.gov.tw/Data/js/Observe/OSM/C/STMap.json',
         'site_obs': 'https://www.cwa.gov.tw/V8/C/W/Observe/MOD/24hr/TBD.html',
         'opendata': ['https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001',
                      'https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001',
                      'https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001']
        }
_REQUESTS_TIMEOUT = 3  # 0.2.5, timeout


_sitemap = {}


def grab(site: str | tuple | list, key: str | None = None) -> dict:
    """grab observed temperature, humidity, and rainfall information from official CWA website
    site   - site name, site id, or site coordinates 
    key    - if the open data API key from CWA is assigned, weather information will be retrieved via the API;
             otherwise, it will be scraped from CWA website only
    return - name of site, observation time, temperature, humidity, and rainfall in dict,
             ex of normal: {'S': '臺北', 'O': '11/02 11:20', 'T': 27.5, 'H': 0.73, 'R': 0.0}
             ex of no info: {}
    """
    
    # prepare site map
    global _sitemap
    if not _sitemap:
        _sitemap = _load_sitemap()

    # confirm and convert input
    site_type = 'name'
    if type(site) == str:
        if site.isascii():
            if site in _sitemap:
                site_type = 'wid'
            elif len(site) == 6:
                site_type = 'aid'
    elif type(site) in (tuple, list) and len(site) == 2 and tuple(map(type, site)) == (float,) * 2:  # 0.2.5
        site_type, site = 'wid', _nearest(site)
    else:
        return {}
    
    grab_web = {'wid': _grab_web_by_siteid, 'name': _grab_web_by_sitename}

    # grab both API and Web if the key is provided
    if key and type(key) == str:
        def _grab_web_wrapper():
            nonlocal infow
            infow = grab_web[site_type](site)

        def _grab_api_wrapper():
            nonlocal infoa
            infoa = _grab_api(site, site_type, key)

        infow, infoa = {}, {}
        tw, ta = None, None
        if site_type in ('name', 'wid'):
            tw = Thread(target=_grab_web_wrapper, daemon=True)
            tw.start()
        if site_type in ('name', 'aid'):
            ta = Thread(target=_grab_api_wrapper, daemon=True)
            ta.start()
        if tw:
            tw.join()
        if ta:
            ta.join()

        return (infoa, infow)[len(infow) > len(infoa)]
    
    # grab Web only if no key is provided
    return grab_web[site_type](site) if site_type != 'aid' else {}

def _grab_api(site, site_type, key):
    SITE_KEYS = {'name': 'StationName', 'aid': 'StationId'}
    site_key = SITE_KEYS[site_type]

    def _grab_api_core(url):
        nonlocal info
        r = requests.get(url, params=params, timeout=_REQUESTS_TIMEOUT)  # 0.2.5, timeout
        if (r.status_code == 200 and r.headers.get('Content-Type').startswith('application/json') and
            (j := r.json()) and (j := j.get('records')) and (sites := j.get('Station'))):
            for s in sites:
                if s.get(site_key) == site:
                    if re := s.get('RainfallElement'):
                        if (v := re.get('Now')) and (v := v.get('Precipitation')) != None:
                            info['R'] = float(v)
                    if we := s.get('WeatherElement'):
                        if v := we.get('AirTemperature'):
                            info['T'] = float(v)
                        if v := we.get('RelativeHumidity'):
                            info['H'] = float(v) / 100
                    if info and (v := s.get('ObsTime')) and (v := v.get('DateTime')):
                        info['O'] = v.replace(':00+08:00', '').replace('T', ' ')
                        if v := s.get('StationName'):
                            info['S'] = v
                        if v:= s.get('StationId'):
                            info['I'] = v
                        if ((v := s.get('GeoInfo')) and
                            (v := v.get('Coordinates')) and
                            len(v) > 1 and
                            (lan := v[1].get('StationLatitude')) and
                            (lon := v[1].get('StationLongitude'))):
                            info['C'] = (float(lan), float(lon))
                        break
        
    params = {'Authorization': key, site_key: site}
    info = {}
    threads = [None] * len(_URLS['opendata'])
    for i, url in enumerate(_URLS['opendata']):
        threads[i] = Thread(target=_grab_api_core, args=(url,), daemon=True)
        threads[i].start()
    for t in threads:
        t.join()

    return info

def _grab_web_by_siteid(siteid):
    info = {}
    r = requests.get(_URLS['site_obs'].replace('TBD', siteid), timeout=_REQUESTS_TIMEOUT)  # 0.2.5, timeout
    if r.status_code == 200:
        soup = BeautifulSoup(r.text, 'html.parser')
        if (v := soup.find(headers='temp')) and (v := (v.find(class_='tem-C'))):  # 0.2.4, to prevent abnormalities
            try:
                info['T'] = float(v.text)
            except:
                ...
        if v := soup.find(headers='hum'):  # 0.2.4, to prevent abnormalities
            try:
                info['H'] = float(v.text)/100
            except:
                ...
        if v := soup.find(headers='rain'):  # 0.2.4, to prevent abnormalities
            try:
                info['R'] = float(v.text)
            except:
                ...
        if info:
            info['I'] = siteid
            if (v := soup.find('tr')) and ((v := v.get('data-cstname')) and type(v) == str):
                info['S'] = v
            if (v := _sitemap.get(siteid)) and (v := v.get('coors')):
                info['C'] = v
            if v := soup.find(headers='time'):
                info['O'] = v.text
    return info
    
def _grab_web_by_sitename(sitename):
    info = {}
    if siteid := _get_siteid(sitename):
        info = _grab_web_by_siteid(siteid)
        if not info.get('S'):
            info['S'] = sitename
    return info

def _get_siteid(site):
    for id_ in _sitemap:
        if site == _sitemap[id_].get('name'):
            return id_

def _load_sitemap():
    sitemap = {}
    r = requests.get(_URLS['site_map'], timeout=_REQUESTS_TIMEOUT)  # 0.2.5, timeout
    if r.status_code == 200 and r.headers.get('Content-Type').startswith('application/json'):
        for s in r.json():
            if (name := s.get('STname')) and (id_ := s.get('ID')):
                sitemap[id_] = {'name': name}
                if (lat := s.get('Lat')) and (lon := s.get('Lon')):
                    sitemap[id_]['coors'] = (float(lat), float(lon))
    return sitemap

def _nearest(coors):
    def eud(coors1, coors2):
        return (coors1[0] - coors2[0]) ** 2 + ((coors1[1] - coors2[1]) ** 2)

    return min((eud(coors, _sitemap[sid]['coors']), sid) for sid in _sitemap)[1]

def tostr(info: dict, sep: str = ', ', show: str | None = 'SOTHR') -> str:
    """translate grabbed weather information to readble str
    info   - grabbed weather information of dict, ex: {'S': '臺北', 'O': '06/07 10:10', 'T': 25.6, 'H': 0.97, 'R': 49.0}
    sep    - separator of each weather information
    show   - control which information to show
             S: station id
             C: coordinates
             O: observation time
             T: temperature
             H: humidity
             R: rainfall
    return - ex of normal: '測站: 臺北, 時間: 06/07 10:10, 溫度: 25.6°C, 濕度: 97%, 雨量: 49.0mm'
             ex of error: '無觀測!' 
    """
    if not info or type(info) != dict:
        return '無觀測'
    
    show_allowed, show_default = 'SICOTHR', 'SOTHR'
    show = ''.join(c for c in show if c in show_allowed) if show and type(show) == str else show_default

    elements = {
        'S': 'S' in info and f'測站: {info["S"]}' or None,
        'I': 'I' in info and f'編號: {info["I"]}' or None,
        'C': 'C' in info and f'座標: ({info["C"][0]}, {info["C"][1]})' or None,
        'O': 'O' in info and f'時間: {info["O"]}' or None,
        'T': 'T' in info and f'溫度: {info["T"]:.1f}℃' or None,
        'H': 'H' in info and f'濕度: {info["H"]:.1%}' or None,
        'R': 'R' in info and f'雨量: {info["R"]:.1f}mm' or None
    }
    toshow = [elements[c] for c in show if c in elements and elements[c]]
    return (type(sep) == str and sep or ', ').join(toshow)

if __name__ == '__main__':
    import argparse
    from time import time

    parser = argparse.ArgumentParser()
    parser.add_argument('site', nargs='+')
    parser.add_argument('--key', '-k')
    parser.add_argument('--show', default='SICOTHR', help='availabe showing indicators are SICOTHR')
    parser.add_argument('--sep', default=', ')
    args = parser.parse_args()
    
    start = time()
    for s in args.site:
        print(tostr(grab(s, args.key), sep=args.sep, show=args.show))
    print(f'{time()-start:.3f}s')
