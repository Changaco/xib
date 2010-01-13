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

from argparse import ArgumentParser as OriginalArgumentParser

class ArgumentParser(OriginalArgumentParser):
	
	class ParseException(Exception): pass
	
	def _print_message(self, message, file=None):
		if not hasattr(self, 'ret'):
			self.ret = ''
		self.ret += message
	
	def exit(self, status=0, message=''):
		if hasattr(self, 'ret') and isinstance(self.ret, basestring):
			raise self.ParseException(status, self.ret+message)
		else:
			raise self.ParseException(status, message)
