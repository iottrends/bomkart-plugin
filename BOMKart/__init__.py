"""
BOMKart - KiCad Plugin
Order electronics components from local Indian distributors directly from KiCad.

https://bomkart.in
A Hallycon Ventures product.
"""

from .bomkart_action import BOMKartPlugin

BOMKartPlugin().register()
