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


commands = ['xmpp-participants', 'irc-participants', 'xmpp-connections', 'irc-connections', 'connections', 'bridges']
admin_commands = ['add-bridge', 'add-xmpp-admin', 'change-bridges-mode', 'debug', 'halt', 'remove-bridges', 'restart-bot', 'restart-bridges', 'stop-bot', 'stop-bridges']

def execute(bot, command_line, bot_admin, bridge):
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
	
	return globals()[command_func](bot, command, args_array, bridge)


def _find_bridges(bot, args_array):
	ret = ''
	b = []
	for arg in args_array:
		try:
			bn = int(arg)
			if bn < 1:
				raise IndexError
			b.append(bot.bridges[bn-1])
		except IndexError:
			ret += '\nInvalid bridge number "'+str(bn)+'".'
		except ValueError:
			found_bridges = bot.findBridges(arg)
			if len(found_bridges) == 0:
				ret += '\nNo bridge found matching "'+arg+'".'
			else:
				b.extend(found_bridges)
	
	if ret != '' or len(b) == 0:
		if ret != '':
			ret += '\n\n'
		ret += bridges(bot, 'bridges', [], None)+'\n\n'
	
	return (b, ret)


def add_bridge(bot, command, args_array, bridge):
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


def add_xmpp_admin(bot, command, args_array, bridge):
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


def bridges(bot, command, args_array, bridge):
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
			ret += ' - this bridge is stopped, use "restart-bridges '+str(i+1)+'" to restart it'
	return ret


def change_bridges_mode(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('bridge_id', nargs='+')
	parser.add_argument('new_mode', choices=Bridge._modes)
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	
	found_bridges, ret = _find_bridges(bot, args.bridge_id)
	for found_bridge in found_bridges:
		r = found_bridge.changeMode(args.new_mode)
		if r:
			ret += r
	
	if ret:
		return ret
	return 'Modes changed.'


def connections(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('--verbose', '-v', default=False, action='store_true')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	return irc_connections(bot, 'irc-connections', args_array, bridge)+'\n'+xmpp_connections(bot, 'xmpp-connections', args_array, bridge)


def debug(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('mode', choices=['on', 'off'])
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		if len(args_array) == 0:
			if bot.debug:
				return 'Debugging is on'
			else:
				return 'Debugging is off'
		else:
			return '\n'+e.args[1]
	
	if args.mode == 'on':
		bot.debug = True
		return 'Debugging is now on'
	else:
		bot.debug = False
		return 'Debugging is now off'


def halt(bot, command, args_array, bridge):
	bot.__del__()
	return


def irc_connections(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('--verbose', '-v', default=False, action='store_true')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	n = len(bot.irc.connections)
	if args.verbose:
		ret = 'List of IRC connections ('+str(n)+'):'
		for c in bot.irc.connections:
			ret += '\n\t'+str(c)
	else:
		ret = 'Number of IRC connections: '+str(n)
	return ret


def irc_participants(bot, command, args_array, bridge):
	ret = ''
	if not bridge:
		for b in bot.bridges:
			irc_participants_nicknames = b.get_participants_nicknames_list(protocols=['irc'])
			ret += '\nparticipants on '+b.irc_room+' at '+b.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
		return ret
	else:
		irc_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['irc'])
		return '\nparticipants on '+bridge.irc_room+' at '+bridge.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)


def remove_bridges(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('bridge_id', nargs='+')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	
	found_bridges, ret = _find_bridges(bot, args.bridge_id)
	
	for found_bridge in found_bridges:
		bot.removeBridge(found_bridge)
	
	return ret+'Bridges removed.'


def restart_bot(bot, command, args_array, bridge):
	bot.restart()
	return

def restart_bridges(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('bridge_id', nargs='+')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	
	found_bridges, ret = _find_bridges(bot, args.bridge_id)
	for found_bridge in found_bridges:
		found_bridge.restart()
	
	return ret+'Bridges restarted.'


def stop_bot(bot, command, args_array, bridge):
	bot.stop()
	return 'Bot stopped.'


def stop_bridges(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('bridge_id', nargs='+')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	
	found_bridges, ret = _find_bridges(bot, args.bridge_id)
	for found_bridge in found_bridges:
		found_bridge.stop()
	
	return ret+'Bridges stopped.'


def xmpp_connections(bot, command, args_array, bridge):
	parser = ArgumentParser(prog=command)
	parser.add_argument('--verbose', '-v', default=False, action='store_true')
	try:
		args = parser.parse_args(args_array)
	except ArgumentParser.ParseException as e:
		return '\n'+e.args[1]
	n = len(bot.xmpp_connections)
	if args.verbose:
		ret = 'List of XMPP connections ('+str(n)+'):'
		for nickname in bot.xmpp_connections.iterkeys():
			ret += '\n\t'+nickname
	else:
		ret = 'Number of XMPP connections: '+str(n)
	return ret


def xmpp_participants(bot, command, args_array, bridge):
	ret = ''
	if not bridge:
		for b in bot.bridges:
			xmpp_participants_nicknames = b.get_participants_nicknames_list(protocols=['xmpp'])
			ret += '\nparticipants on '+b.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
		return ret
	else:
		xmpp_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['xmpp'])
		return '\nparticipants on '+bridge.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
