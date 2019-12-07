# -*- coding: utf-8 -*-
"""
Init and user authentication in Earth Engine
"""
import urllib.request
import webbrowser
from qgis.PyQt.QtWidgets import QInputDialog

import ee


def init():
    try:
        ee.Initialize()
    except ee.ee_exception.EEException:
        authenticate()
        ee.Initialize()  # retry initialization once the user logs in


def tiny_url(url):
    apiurl = "http://tinyurl.com/api-create.php?url="
    tinyurl = urllib.request.urlopen(apiurl + url).read()
    return tinyurl.decode("utf-8")


def authenticate():
    auth_url = ee.oauth.get_authorization_url()
    webbrowser.open_new(auth_url)

    print('\nEarth Engine Authentication:\n'
          'If the web browser does not start automatically, '
          'please manually browse the URL below:\n"{}"'.format(auth_url))

    token, ok = QInputDialog.getText(None, 'Authorization',
                                     'Google Earth Engine Python is not detected on this machine.\n'
                                     'This plugin uses Google Earth Engine Python API and requires users \n'
                                     'to be authorized, please follow the instructions in the opened web page\n'
                                     'and paste the resulting auth token here.\n\n'
                                     'If the web page does not open automatically, visit the following link manually:\n'
                                     'URL: ' + tiny_url(auth_url))
    if ok and token:
        ee.oauth._obtain_and_write_token(token.strip())
