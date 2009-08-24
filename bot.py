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


# *** Versioning ***
# Major will pass to 1 when xib will be considered fault-tolerant
# After that major will only be changed if the new version is not retro-compatible (e.g. requires changes in config file)

version = 0, 1


import irclib
import xmppony as xmpp
import threading
from bridge import *
from time import sleep
import re
import sys
import xml.parsers.expat


class bot(Thread):
	
	def __init__(self, jid, password, nickname, error_fd=sys.stderr, debug=False):
		Thread.__init__(self)
		self.commands = ['!xmpp_participants', '!irc_participants']
		self.bare_jid = xmpp.protocol.JID(jid=jid)
		self.bare_jid.setResource('')
		self.nickname = nickname
		self.password = password
		self.error_fd = error_fd
		self.debug = debug
		self.bridges = []
		self.xmpp_connections = {}
		self.irc = irclib.IRC()
		self.irc.bot = self
		self.irc.add_global_handler('all_events', self._irc_event_handler)
		self.irc_thread = Thread(target=self.irc.process_forever)
		self.irc_thread.start()
		# Open connection with XMPP server
		try:
			self.xmpp_c = self.get_xmpp_connection(self.nickname)
		except:
			self.error('[Error] XMPP Connection failed')
			raise
		self.xmpp_thread = Thread(target=self._xmpp_loop)
		self.xmpp_thread.start()
	
	
	def error(self, s, debug=False):
		"""Output an error message."""
		if not debug or debug and self.debug:
			try:
				self.error_fd.write(auto_encode(s)+"\n")
			except EncodingException:
				self.error_fd.write('Error message cannot be transcoded.\n')
	
	
	def _xmpp_loop(self):
		"""[Internal] XMPP infinite loop."""
		while True:
			try:
				try:
					if len(self.xmpp_connections) == 1:
						sleep(0.5)  # avoid bot connection being locked all the time
					for c in self.xmpp_connections.itervalues():
						if hasattr(c, 'Process'):
							c.lock.acquire()
							c.Process(0.5)
							c.lock.release()
						else:
							sleep(0.5)
				except RuntimeError:
					pass
			except (xml.parsers.expat.ExpatError, xmpp.protocol.XMLNotWellFormed):
				self.error('=> Debug: received invalid stanza', debug=True)
				continue
	
	
	def _xmpp_presence_handler(self, dispatcher, presence):
		"""[Internal] Manage XMPP presence."""
		
		xmpp_c = dispatcher._owner
		
		if xmpp_c.nickname != self.nickname:
			self.error('=> Debug: Skipping XMPP presence not received on bot connection.', debug=True)
			return
		
		self.error('==> Debug: Received XMPP presence.', debug=True)
		self.error(presence.__str__(fancy=1), debug=True)
		
		from_ = xmpp.protocol.JID(presence.getFrom())
		bare_jid = unicode(from_.getNode()+'@'+from_.getDomain())
		for bridge in self.bridges:
			if bare_jid == bridge.xmpp_room.room_jid:
				# presence comes from a muc
				resource = unicode(from_.getResource())
				
				if resource == '':
					# presence comes from the muc itself
					# TODO: handle room deletion and muc server reboot
					pass
				
				else:
					# presence comes from a participant of the muc
					try:
						p = bridge.getParticipant(resource)
						
					except NoSuchParticipantException:
						if presence.getType() != 'unavailable' and resource != bridge.bot.nickname:
							bridge.addParticipant('xmpp', resource)
						return
					
					
					if p.protocol == 'xmpp' and presence.getType() == 'unavailable':
						x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
						if x and x.getTag('status', attrs={'code': '303'}):
							# participant changed its nickname
							item = x.getTag('item')
							if not item:
								self.error('=> Debug: bad stanza, no item element', debug=True)
								return
							new_nick = item.getAttr('nick')
							if not new_nick:
								self.error('=> Debug: bad stanza, new nick is not given', debug=True)
								return
							p.changeNickname(new_nick, 'irc')
						
						else:
							# participant left
							bridge.removeParticipant('xmpp', resource, presence.getStatus())
					
				return
	
	
	def _xmpp_iq_handler(self, dispatcher, iq):
		"""[Internal] Manage XMPP IQs."""
		self.error('=> Debug: Received XMPP iq.', debug=True)
		self.error(iq.__str__(fancy=1), debug=True)
	
	
	def _xmpp_message_handler(self, dispatcher, message):
		"""[Internal] Manage XMPP messages."""
		
		xmpp_c = dispatcher._owner
		
		if message.getBody() == None:
			return
		
		if message.getType() == 'chat':
			self.error('==> Debug: Received XMPP chat message.', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
			from_bare_jid = unicode(message.getFrom().getNode()+'@'+message.getFrom().getDomain())
			for bridge in self.bridges:
				if from_bare_jid == bridge.xmpp_room.room_jid:
					# message comes from a room participant
					
					try:
						from_ = bridge.getParticipant(message.getFrom().getResource())
						to_ = bridge.getParticipant(xmpp_c.nickname)
						
						from_.sayOnIRCTo(to_.nickname, message.getBody())
						
					except NoSuchParticipantException:
						if xmpp_c.nickname == self.nickname:
							xmpp_c.send(xmpp.protocol.Message(to=message.getFrom(), body=self.respond(message.getBody(), participant=from_), typ='chat'))
							return
						self.error('=> Debug: XMPP chat message not relayed', debug=True)
						return
		
		elif message.getType() == 'groupchat':
			# message comes from a room
			
			for child in message.getChildren():
				if child.getName() == 'delay':
					# MUC delayed message
					return
			
			if xmpp_c.nickname != self.nickname:
				self.error('=> Debug: Ignoring XMPP MUC message not received on bot connection.', debug=True)
				return
			
			
			from_ = xmpp.protocol.JID(message.getFrom())
			
			if unicode(from_.getResource()) == self.nickname:
				self.error('=> Debug: Ignoring XMPP MUC message sent by self.', debug=True)
				return
			
			room_jid = unicode(from_.getNode()+'@'+from_.getDomain())
			for bridge in self.bridges:
				if room_jid == bridge.xmpp_room.room_jid:
					resource = unicode(from_.getResource())
					if resource == '':
						# message comes from the room itself
						self.error('=> Debug: Ignoring XMPP groupchat message sent by the room.', debug=True)
						return
					else:
						# message comes from a participant of the room
						self.error('==> Debug: Received XMPP groupchat message.', debug=True)
						self.error(message.__str__(fancy=1), debug=True)
						
						try:
							participant_ = bridge.getParticipant(resource)
						except NoSuchParticipantException:
							if resource != self.nickname:
								self.error('=> Debug: NoSuchParticipantException "'+resource+'" on "'+str(bridge)+'", WTF ?', debug=True)
							return
						
						participant_.sayOnIRC(message.getBody())
						return
		
		else:
			self.error('==> Debug: Received XMPP message of unknown type "'+message.getType()+'".', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
	
	
	def _irc_event_handler(self, connection, event):
		"""[Internal] Manage IRC events"""
		
		# Answer ping
		if event.eventtype() == 'ping':
			connection.pong(connection.get_server_name())
			return
		
		
		# Events we always want to ignore
		if 'all' in event.eventtype() or 'motd' in event.eventtype():
			return
		if event.eventtype() in ['pong', 'privnotice', 'ctcp', 'nochanmodes', 'notexttosend', 'currenttopic', 'topicinfo']:
			self.error('=> Debug: ignoring '+event.eventtype(), debug=True)
			return
		
		
		nickname = None
		if event.source() != None:
			if '!' in event.source():
				nickname = event.source().split('!')[0]
		
		
		# Events that we want to ignore only in some cases
		if event.eventtype() in ['umode', 'welcome', 'yourhost', 'created', 'myinfo', 'featurelist', 'luserclient', 'luserop', 'luserchannels', 'luserme', 'n_local', 'n_global', 'endofnames', 'luserunknown', 'luserconns']:
			if connection.really_connected == False:
				if event.target() == connection.nickname:
					connection.really_connected = True
					connection._call_nick_callbacks(None)
				elif len(connection.nick_callbacks) > 0:
					self.error('===> Debug: event target ('+event.target()+') and connection nickname ('+connection.nickname+') don\'t match')
					connection._call_nick_callbacks('nicknametoolong', arguments=[len(event.target())])
			self.error('=> Debug: ignoring '+event.eventtype(), debug=True)
			return
		
		
		# A string representation of the event
		event_str = '==> Debug: Received IRC event.\nconnection='+str(connection)+'\neventtype='+event.eventtype()+'\nsource='+str(event.source())+'\ntarget='+str(event.target())+'\narguments='+str(event.arguments())
		
		
		if event.eventtype() in ['pubmsg', 'action', 'privmsg', 'quit', 'part', 'nick']:
			if nickname == None:
				return
			
			handled = False
			
			if event.eventtype() in ['quit', 'part', 'nick']:
				self.error(event_str, debug=True)
			
			# TODO: lock self.bridges for thread safety
			for bridge in self.bridges:
				if connection.server != bridge.irc_server:
					continue
				
				try:
					from_ = bridge.getParticipant(nickname)
					
				except NoSuchParticipantException:
					continue
				
				
				# Private message
				if event.eventtype() == 'privmsg':
					if event.target() == None:
						return
					
					try:
						to_ = bridge.getParticipant(event.target().split('!')[0])
						self.error(event_str, debug=True)
						from_.sayOnXMPPTo(to_.nickname, event.arguments()[0])
						return
						
					except NoSuchParticipantException:
						if event.target().split('!')[0] == self.nickname:
							# Message is for the bot
							self.error(event_str, debug=True)
							connection.privmsg(from_.nickname, self.respond(event.arguments()[0]))
							return
						else:
							continue
				
				
				# From here we skip if the event was not received on bot connection
				if connection.get_nickname() != self.nickname:
					self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
					continue
				
				
				# Leaving events
				if event.eventtype() == 'quit' or event.eventtype() == 'part' and event.target() == bridge.irc_room:
					if len(event.arguments()) > 0:
						leave_message = event.arguments()[0]
					elif event.eventtype() == 'quit':
						leave_message = 'Left server.'
					elif event.eventtype() == 'part':
						leave_message = 'Left channel.'
					else:
						leave_message = ''
					bridge.removeParticipant('irc', from_.nickname, leave_message)
					handled = True
					continue
				
				
				# Nickname change
				if event.eventtype() == 'nick':
					from_.changeNickname(event.target(), 'xmpp')
					handled = True
					continue
				
				
				# Chan message
				if event.eventtype() in ['pubmsg', 'action']:
					if bridge.irc_room == event.target() and bridge.irc_server == connection.server:
						self.error(event_str, debug=True)
						message = event.arguments()[0]
						if event.eventtype() == 'action':
							message = '/me '+message
						from_.sayOnXMPP(message)
						return
					else:
						continue
			
			if handled == False:
				if not event.eventtype() in ['quit', 'part', 'nick']:
					self.error(event_str, debug=True)
				self.error('=> Debug: event was not handled')
			return
		
		
		if event.eventtype() in ['namreply', 'join']:
			if connection.get_nickname() != self.nickname:
				self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
				return
			
			if event.eventtype() == 'namreply':
				# TODO: lock self.bridges for thread safety
				for bridge in self.getBridges(irc_room=event.arguments()[1], irc_server=connection.server):
					for nickname in re.split('(?:^[&@\+]?|(?: [&@\+]?)*)', event.arguments()[2].strip()):
						if nickname == '' or nickname == self.nickname:
							continue
						bridge.addParticipant('irc', nickname)
				return
			elif event.eventtype() == 'join':
				bridges = self.getBridges(irc_room=event.target(), irc_server=connection.server)
				if len(bridges) == 0:
					self.error('===> Debug: no bridge found for "'+event.target()+' at '+connection.server+'"', debug=True)
					return
				for bridge in bridges:
					bridge.addParticipant('irc', nickname)
				return
		
		
		# From here the event is shown
		self.error(event_str, debug=True)
		
		if event.eventtype() == 'disconnect':
			# TODO: lock self.bridges for thread safety
			for bridge in self.bridges:
				try:
					bridge.getParticipant(connection.get_nickname())
					if bridge.mode == 'normal':
						bridge.switchFromNormalToLimitedMode()
				except NoSuchParticipantException:
					pass
			return
		elif event.eventtype() == 'nicknameinuse':
			connection._call_nick_callbacks('nicknameinuse')
			return
		elif event.eventtype() == 'erroneusnickname':
			connection._call_nick_callbacks('erroneusnickname')
			return
		
		
		# Unhandled events
		self.error('=> Debug: event not handled', debug=True)
	
	
	def new_bridge(self, xmpp_room, irc_room, irc_server, mode, say_level, irc_port=6667):
		"""Create a bridge between xmpp_room and irc_room at irc_server."""
		b = bridge(self, xmpp_room, irc_room, irc_server, mode, say_level, irc_port=irc_port)
		self.bridges.append(b)
		return b
	
	
	def getBridges(self, irc_room=None, irc_server=None, xmpp_room_jid=None):
		bridges = [b for b in self.bridges]
		for bridge in [b for b in bridges]:
			if irc_room != None and bridge.irc_room != irc_room:
				bridges.remove(bridge)
				continue
			if irc_server != None and bridge.irc_server != irc_server:
				bridges.remove(bridge)
				continue
			if xmpp_room_jid != None and bridge.xmpp_room.room_jid != xmpp_room_jid:
				bridges.remove(bridge)
				continue
		return bridges
	
	
	def get_xmpp_connection(self, nickname):
		if self.xmpp_connections.has_key(nickname):
			c = self.xmpp_connections[nickname]
			c.used_by += 1
			self.error('===> Debug: using existing XMPP connection for "'+nickname+'", now used by '+str(c.used_by)+' bridges', debug=True)
			return c
		self.error('===> Debug: opening new XMPP connection for "'+nickname+'"', debug=True)
		c = xmpp.client.Client(self.bare_jid.getDomain(), debug=[])
		c.lock = threading.RLock()
		c.lock.acquire()
		self.xmpp_connections[nickname] = c
		c.used_by = 1
		c.nickname = nickname
		c.connect()
		c.auth(self.bare_jid.getNode(), self.password)
		c.RegisterHandler('presence', self._xmpp_presence_handler)
		c.RegisterHandler('iq', self._xmpp_iq_handler)
		c.RegisterHandler('message', self._xmpp_message_handler)
		c.sendInitPresence()
		c.lock.release()
		return c
	
	
	def close_xmpp_connection(self, nickname):
		if not self.xmpp_connections.has_key(nickname):
			return
		c = self.xmpp_connections[nickname]
		c.used_by -= 1
		if c.used_by < 1:
			self.error('===> Debug: closing XMPP connection for "'+nickname+'"', debug=True)
			self.xmpp_connections.pop(nickname)
			del c
		else:
			self.error('===> Debug: XMPP connection for "'+nickname+'" is now used by '+str(c.used_by)+' bridges', debug=True)
	
	
	def removeBridge(self, bridge):
		self.bridges.remove(bridge)
		del bridge
	
	
	def respond(self, message, participant=None):
		ret = ''
		if message.strip() == '!xmpp_participants':
			if participant == None:
				for bridge in self.bridges:
					xmpp_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['xmpp'])
					ret += '\nparticipants on '+bridge.xmpp_room.room_jid+': '+' '.join(xmpp_participants_nicknames)
				return ret
			else:
				xmpp_participants_nicknames = participant.bridge.get_participants_nicknames_list(protocols=['xmpp'])
				return 'participants on '+participant.bridge.xmpp_room.room_jid+': '+' '.join(xmpp_participants_nicknames)
		elif message.strip() == '!irc_participants':
			if participant == None:
				for bridge in self.bridges:
					irc_participants_nicknames = bridge.get_participants_nicknames_list(protocols=['irc'])
					ret += '\nparticipants on '+bridge.irc_room+' at '+bridge.irc_server+': '+' '.join(irc_participants_nicknames)
				return ret
			else:
				irc_participants_nicknames = participant.bridge.get_participants_nicknames_list(protocols=['irc'])
				return 'participants on '+participant.bridge.irc_room+' at '+participant.bridge.irc_server+': '+' '.join(irc_participants_nicknames)
		else:
			return 'commands: '+' '.join(self.commands)
	
	
	def __del__(self):
		for bridge in self.bridges:
			self.removeBridge(bridge)