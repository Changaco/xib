#!/usr/bin/env python
# -*- coding: utf-8 -*-


# *** LICENSE ***
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


from xml.dom.minidom import parse
from time import sleep
import sys
import traceback

from bot import Bot


try:
	if len(sys.argv) > 1:
		config = parse(sys.argv[1])
	else:
		config = parse('config.xml')
except IOError:
	print '[Error] configuration file is missing or cannot be read'
	quit(1)

bots_jids = []
for bot_el in config.getElementsByTagName('bot'):
	if bot_el.getAttribute('jid') in bots_jids:
		print '[Error] you cannot have two bots using the same JID'
		quit(2)
	bots_jids.append(bot_el.getAttribute('jid'))



bots = []
for bot_el in config.getElementsByTagName('bot'):
	debug = False
	if bot_el.hasAttribute('debug'):
		if bot_el.getAttribute('debug') == 'true':
			debug = True
	admins_jid = []
	for admin_el in bot_el.getElementsByTagName('admin'):
		if admin_el.hasAttribute('jid'):
			admins_jid.append(admin_el.getAttribute('jid'))
	bot = Bot(bot_el.getAttribute('jid'), bot_el.getAttribute('password'), bot_el.getAttribute('nickname'), admins_jid=admins_jid, debug=debug)
	bots.append(bot)
	for bridge_el in bot_el.getElementsByTagName('bridge'):
		xmpp_room = bridge_el.getElementsByTagName('xmpp-room')[0]
		irc = bridge_el.getElementsByTagName('irc')[0]
		
		irc_connection_interval = 1
		if irc.hasAttribute('connection_interval'):
			try:
				irc_connection_interval = float(irc.getAttribute('connection_interval'))
			except ValueError:
				print '[Error] the value of connection_interval must be a number'
		
		if bridge_el.hasAttribute('say_level'):
			say_level = bridge_el.getAttribute('say_level')
		else:
			say_level = 'all'
		
		if bridge_el.hasAttribute('mode'):
			mode = bridge_el.getAttribute('mode')
		else:
			mode = 'normal'
		
		bot.new_bridge(xmpp_room.getAttribute('jid'), irc.getAttribute('chan'), irc.getAttribute('server'), mode, say_level, irc_connection_interval=irc_connection_interval)

try:
	if len(bots) == 0:
		print 'No bots in the configuration file, exiting ...'
		exit(0)
	
	while True:
		for bot in bots:
			if bot.halt and len(bot.xmpp_connections) == 0:
				bots.remove(bot)
		if len(bots) == 0:
			raise Exception()
		sleep(10)
except:
	if len(bots) == 0:
		print 'All bots have been shut down, exiting ...'
		exit(0)
	
	for bot in bots:
		bots.remove(bot)
		del bot
	traceback.print_exc()
	quit(3)