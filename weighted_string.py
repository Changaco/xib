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


class Weighted_string(unicode):
	
	def __new__(cls, weight, string):
		return super(Weighted_string, cls).__new__(cls, unicode(string))
	
	def __init__(self, weight, string):
		self.weight = weight
	
	def __lt__(self, other):
		return self.weight < other
	
	def __le__(self, other):
		return self.weight <= other
	
	def __eq__(self, other):
		return self.weight == other
	
	def __ne__(self, other):
		return self.weight != other
	
	def __gt__(self, other):
		return self.weight > other
	
	def __ge__(self, other):
		return self.weight >= other
	
	def __repr__(self):
		return '<'+unicode(self.weight)+', '+unicode.__repr__(self)+'>'
