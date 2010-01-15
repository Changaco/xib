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


import shlex

from argparse_modified import ArgumentParser
import muc
xmpp = muc.xmpp
del muc

from bridge import Bridge


commands = ['xmpp-participants', 'irc-participants', 'bridges']
admin_commands = ['add-bridge', 'add-xmpp-admin', 'change-bridge-mode', 'halt', 'remove-bridge', 'restart-bot', 'restart-bridge', 'stop-bridge']

def execute(bot, command_line, bot_admin, bridge):
	ret = ''
	command = shlex.split(command_line)
	if len(command) > 1:
		args_array = command[1:]
	else:
		args_array = []
	command = command[0]
	command_func = command.replace('-', '_')
	
	if not globals().has_key(command_func):
		ret = 'Error: "'+command+'" is not a valid command.\ncommands:  '+'  '.join(commands)
		if bot_admin:
			return ret+'\n'+'admin commands:  '+'  '.join(admin_commands)
		else:
			return ret
	elif command in admin_commands and not bot_admin:
		return 'You have to be a bot admin to use this command.'
	
	b = bridge
	
	if command in ['change-bridge-mode', 'remove-bridge', 'restart-bridge', 'stop-bridge']:
		# we need to know which bridge the command is for
		if len(args_array) == 0:
			if bridge:
				b = bridge
			else:
				return 'You must specify a bridge. '+bridges(bot, 'bridges', [], bot_admin, None)
		else:
			try:
				bn = int(args_array[0])
				if bn < 1:
					raise IndexError
				b = bot.bridges[bn-1]
			except IndexError:
				return 'Invalid bridge number "'+str(bn)+'". '+bridges(bot, 'bridges', [], bot_admin, None)
			except ValueError:
				bridges = bot.findBridges(args_array[0])
				if len(bridges) == 0:
					return 'No bridge found matching "'+args_array[0]+'". '+bridges(bot, 'bridges', [], bot_admin, None)
				elif len(bridges) == 1:
					b = bridges[0]
				elif len(bridges) > 1:
					return 'More than one bridge matches "'+args_array[0]+'", please be more specific. '+bridges(bot, 'bridges', [], bot_admin, None)
	
	
	return globals()[command_func](bot, command, args_array, bot_admin, b)


def add_bridge(bot, command, args_array, bot_admin, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('xmpp_room_jid', type=str)
	parser.add_argument('irc_chan', type=str)
	parser.add_argument('irc_server', type=str)
	parser.add_argument('--mode', choices=Bridge._modes, default='normal')
	parser.add_argument('--say-level', choices=Bridge._say_levels, default='all')
	parser.add_argument('--irc-port', type=int, default=6667)
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	
	bot.new_bridge(args.xmpp_room_jid, args.irc_chan, args.irc_server, args.mode, args.say_level, irc_port=args.irc_port)
	
	return 'Bridge added.'


def add_xmpp_admin(bot, command, args_array, bot_admin, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('jid', type=str)
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	bot.admins_jid.append(args.jid)
	for b in bot.bridges:
		for p in b.participants:
			if p.real_jid != None and xmpp.protocol.JID(args.jid).bareMatch(p.real_jid):
				p.bot_admin = True
	
	return 'XMPP admin added.'


def bridges(bot, command, args_array, bot_admin, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('--show-mode', default=False, action='store_true')
	parser.add_argument('--show-say-level', default=False, action='store_true')
	parser.add_argument('--show-participants', default=False, action='store_true')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	ret = 'List of bridges:'
	for i, b in enumerate(bot.bridges):
		ret += '\n'+str(i+1)+' - '+str(b)
		if args.show_mode:
			ret += ' - mode='+b.mode
		if args.show_say_level:
			ret += ' - say_level='+Bridge._say_levels[b.say_level]
		if args.show_participants:
			xmpp_participants_nicknames = b.get_participants_nicknames_list(protocols=['xmpp'])
			ret += '\nparticipants on XMPP ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
			irc_participants_nicknames = b.get_participants_nicknames_list(protocols=['irc'])
			ret += '\nparticipants on IRC ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
		if b.irc_connection == None:
			ret += ' - this bridge is stopped, use "restart-bridge '+str(i+1)+'" to restart it'
	return ret


def change_bridge_mode(bot, command, args_array, bot_admin, bridge):
	new_mode = args_array[1]
	if not new_mode in Bridge._modes:
		return '"'+new_mode+'" is not a valid mode, list of modes: '+' '.join(Bridge._modes)
	r = bridge.changeMode(new_mode)
	if r:
		return r
	return 'Mode changed.'


def halt(bot, command, args_array, bot_admin, bridge):
	bot.stop()
	return


def irc_participants(bot, command, args_array, bot_admin, bridge):
	if not bridge:
		for b in bot.bridges:
			irc_participants_nicknames = b.get_participants_nicknames_list(protocols=['irc'])
			ret += '\nparticipants on '+b.irc_room+' at '+b.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
		return ret
	else:
		irc_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['irc'])
		return '\nparticipants on '+bridge.irc_room+' at '+bridge.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)


def remove_bridge(bot, command, args_array, bot_admin, bridge):
	bot.removeBridge(bridge)
	return 'Bridge removed.'


def restart_bot(bot, command, args_array, bot_admin, bridge):
	bot.restart()
	return

def restart_bridge(bot, command, args_array, bot_admin, bridge):
	bridge.restart()
	return 'Bridge restarted.'


def stop_bridge(bot, command, args_array, bot_admin, bridge):
	bridge.stop()
	return 'Bridge stopped.'


def xmpp_participants(bot, command, args_array, bot_admin, bridge):
	if not bridge:
		for b in bot.bridges:
			xmpp_participants_nicknames = b.get_participants_nicknames_list(protocols=['xmpp'])
			ret += '\nparticipants on '+b.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
		return ret
	else:
		xmpp_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['xmpp'])
		return '\nparticipants on '+bridge.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
