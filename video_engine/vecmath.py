"""3D vector helpers shared by the camera modules.

Vectors are plain 3-tuples of floats. `length` returns the scalar length; `unit` returns the normalized
vector. (Two modules previously defined `_norm` with opposite meanings - one the length, one the unit vector -
which is why the names here are explicit.)
"""
from __future__ import annotations


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return dot(a, a) ** 0.5


def unit(a):
    n = length(a)
    return (a[0] / n, a[1] / n, a[2] / n)
