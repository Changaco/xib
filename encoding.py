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


class EncodingException(Exception): pass


def auto_encode(s):
	for codec in ['utf-8', 'iso8859_15']:
		try:
			return s.encode(codec)
		except (UnicodeEncodeError, UnicodeDecodeError) as e:
			print e
			pass
	raise EncodingException('no suitable codec found')


def auto_decode(s):
	for codec in ['utf-8', 'iso8859_15']:
		try:
			return s.decode(codec)
		except (UnicodeEncodeError, UnicodeDecodeError) as e:
			print e
			pass
	raise EncodingException('no suitable codec found')