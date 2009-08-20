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


from bot import bot
from time import sleep
from xml.dom.minidom import parse
import sys
import traceback



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


try:
	bots = []
	for bot_el in config.getElementsByTagName('bot'):
		debug = False
		if bot_el.hasAttribute('debug'):
			if bot_el.getAttribute('debug') == 'true':
				debug = True
		bot_ = bot(bot_el.getAttribute('jid'), bot_el.getAttribute('password'), bot_el.getAttribute('nickname'), debug=debug)
		bots.append(bot_)
		for bridge_el in bot_el.getElementsByTagName('bridge'):
			xmpp_room = bridge_el.getElementsByTagName('xmpp-room')[0]
			irc = bridge_el.getElementsByTagName('irc')[0]
			say_participants_list = True
			if bridge_el.hasAttribute('say_participants_list'):
				if bridge_el.getAttribute('say_participants_list') == 'false':
					say_participants_list = False
			if bridge_el.hasAttribute('mode'):
				mode = bridge_el.getAttribute('mode')
			else:
				mode = 'normal'
			bridge_ = bot_.new_bridge(xmpp_room.getAttribute('jid'), irc.getAttribute('chan'), irc.getAttribute('server'), mode, say_participants_list)
	
	
	while True:
		sleep(1)
except:
	for bot in bots:
		del bot
	traceback.print_exc()
	quit(3)