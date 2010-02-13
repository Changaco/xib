#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


from weighted_string import Weighted_string

debug = Weighted_string(0, 'debug')
info = Weighted_string(1, 'info')
notice = Weighted_string(2, 'notice')
warning = Weighted_string(3, 'warning')
error = Weighted_string(4, 'error')
nothing = Weighted_string(5, 'nothing')
levels = ['debug', 'info', 'notice', 'warning', 'error', 'nothing']

def get(level):
	if isinstance(level, int):
		return globals()[levels[level]]
	elif isinstance(level, basestring):
		try:
			return globals()[str(level)]
		except KeyError:
			raise ValueError, 'say_level must be one of these values: '+', '.join(levels)
	else:
		raise TypeError, 'say_level must be either an int or a string'
