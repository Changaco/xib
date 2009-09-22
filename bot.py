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
import muc
xmpp = muc.xmpp
del muc
import threading
from bridge import *
from time import sleep
import re
import sys
import xml.parsers.expat
import traceback


class bot(Thread):
	
	def __init__(self, jid, password, nickname, admins_jid=[], error_fd=sys.stderr, debug=False):
		Thread.__init__(self)
		self.commands = ['!xmpp_participants', '!irc_participants']
		self.bare_jid = xmpp.protocol.JID(jid=jid)
		self.bare_jid.setResource('')
		self.nickname = nickname
		self.password = password
		self.error_fd = error_fd
		self.debug = debug
		self.admins_jid = admins_jid
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
	
	
	def error(self, s, debug=False, send_to_admins=False):
		"""Output an error message."""
		if send_to_admins == True:
			self._send_message_to_admins(s)
		if not debug or debug and self.debug:
			try:
				self.error_fd.write(auto_encode(s)+"\n")
			except EncodingException:
				self.error_fd.write('Error message cannot be transcoded.\n')
	
	
	def _xmpp_loop(self):
		"""[Internal] XMPP infinite loop."""
		i = 1
		while True:
			unlock = False
			try:
				if len(self.xmpp_connections) == 1:
					sleep(0.5)  # avoid bot connection being locked all the time
				j = 0
				for c in self.xmpp_connections.itervalues():
					i += 1
					j += 1
					if hasattr(c, 'lock'):
						c.lock.acquire()
						if i == j:
							ping = xmpp.protocol.Iq(typ='get')
							ping.addChild(name='ping', namespace='urn:xmpp:ping')
							self.error('=> Debug: sending XMPP ping', debug=True)
							c.pings.append(c.send(ping))
						if hasattr(c, 'Process'):
							c.Process(0.01)
						c.lock.release()
					if i > 5000:
						i = 0
			except RuntimeError:
				pass
			except (xml.parsers.expat.ExpatError, xmpp.protocol.XMLNotWellFormed):
				self.error('=> Debug: invalid stanza', debug=True)
				unlock = True
			except xmpp.Conflict:
				c.reconnectAndReauth()
				for m in c.mucs:
					m.rejoin()
				unlock = True
			except:
				error = '[Error] Unkonwn exception on XMPP thread:\n'
				error += traceback.format_exc()
				self.error(error, send_to_admins=True)
				unlock = True
			if unlock == True:
				c.lock.release()
	
	
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
						p = None
						p = bridge.getParticipant(resource)
						
					except NoSuchParticipantException:
						if presence.getType() != 'unavailable' and resource != bridge.bot.nickname:
							bridge.addParticipant('xmpp', resource)
							return
						elif resource == bridge.bot.nickname:
							pass
						else:
							return
					
					
					if presence.getType() == 'unavailable':
						x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
						item = None
						if x:
							item = x.getTag('item')
						if x and x.getTag('status', attrs={'code': '303'}):
							# participant changed its nickname
							if p == None:
								return
							if p.protocol != 'xmpp':
								return
							item = x.getTag('item')
							if not item:
								self.error('=> Debug: bad stanza, no item element', debug=True)
								return
							new_nick = item.getAttr('nick')
							if not new_nick:
								self.error('=> Debug: bad stanza, new nick is not given', debug=True)
								return
							p.changeNickname(new_nick, 'irc')
							
						elif x and x.getTag('status', attrs={'code': '307'}):
							# participant was kicked
							if p == None:
								bridge.xmpp_room.rejoin()
								return
							if isinstance(p.xmpp_c, xmpp.client.Client):
								p.muc.rejoin()
							else:
								if item:
									reason = item.getTag('reason')
									actor = item.getTag('actor')
									if actor and actor.has_attr('jid'):
										kicker = actor.getAttr('jid')
										s1 = 'Kicked by '+kicker
									else:
										s1 = 'Kicked from XMPP'
									if reason:
										s2 = ' with reason: '+reason.getData()
									else:
										s2 = ' (no reason was given)'
								else:
									s1 = 'Kicked from XMPP'
									s2 = ' (no reason was given)'
								
								bridge.removeParticipant('xmpp', p.nickname, s1+s2)
							
						elif x and x.getTag('status', attrs={'code': '301'}):
							# participant was banned
							if p == None:
								m = '[Error] bot got banned from XMPP'
								self.error(m)
								bridge.say(m, on_xmpp=False)
								self.removeBridge(bridge)
								return
							if item:
								reason = item.getTag('reason')
								actor = item.getTag('actor')
								if actor and actor.has_attr('jid'):
									kicker = actor.getAttr('jid')
									s1 = 'Banned by '+kicker
								else:
									s1 = 'Banned from XMPP'
								if reason:
									s2 = ' with reason: '+reason.getData()
								else:
									s2 = ' (no reason was given)'
							else:
								s1 = 'Banned from XMPP'
								s2 = ' (no reason was given)'
							
							bridge.removeParticipant('xmpp', p.nickname, s1+s2)
							
						else:
							# participant left
							bridge.removeParticipant('xmpp', resource, presence.getStatus())
					
				return
	
	
	def _xmpp_iq_handler(self, dispatcher, iq):
		"""[Internal] Manage XMPP IQs."""
		
		xmpp_c = dispatcher._owner
		
		# Ignore pongs
		if iq.getType() in ['result', 'error'] and iq.getID() in xmpp_c.pings:
			xmpp_c.pings.remove(iq.getID())
			self.error('=> Debug: received XMPP pong', debug=True)
			return
		
		self.error('==> Debug: Received XMPP iq.', debug=True)
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
			self.error('=> Debug: ignoring IRC '+event.eventtype(), debug=True)
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
		event_str = '==> Debug: Received IRC event.\nconnection='+connection.__str__()+'\neventtype='+event.eventtype()+'\nsource='+auto_decode(event.source().__str__())+'\ntarget='+auto_decode(event.target().__str__())+'\narguments='+auto_decode(event.arguments().__str__())
		
		
		if event.eventtype() in ['pubmsg', 'action', 'privmsg', 'quit', 'part', 'nick', 'kick']:
			if nickname == None:
				return
			
			handled = False
			
			if event.eventtype() in ['quit', 'part', 'nick', 'kick']:
				if connection.get_nickname() != self.nickname:
					self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bot connection', debug=True)
					return
				else:
					self.error(event_str, debug=True)
			
			if event.eventtype() == 'kick' and len(event.arguments()) < 1:
				self.error('=> Debug: length of arguments should be greater than 0 for a '+event.eventtype()+' event')
				return
			
			if event.eventtype() in ['pubmsg', 'action']:
				if connection.get_nickname() != self.nickname:
					self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bot connection', debug=True)
					return
				if nickname == self.nickname:
					self.error('=> Debug: ignoring IRC '+event.eventtype()+' sent by self', debug=True)
					return
			
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
				
				
				# kick handling
				if event.eventtype() == 'kick':
					if event.target().lower() == bridge.irc_room:
						try:
							kicked = bridge.getParticipant(event.arguments()[0])
							if isinstance(kicked.irc_connection, irclib.ServerConnection):
								kicked.irc_connection.join(bridge.irc_room)
							else:
								if len(event.arguments()) > 1:
									bridge.removeParticipant('irc', kicked.nickname, 'Kicked by '+nickname+' with reason: '+event.arguments()[1])
								else:
									bridge.removeParticipant('irc', kicked.nickname, 'Kicked by '+nickname+' (no reason was given)')
							return
						except NoSuchParticipantException:
							self.error('=> Debug: a participant that was not here has been kicked ? WTF ?')
							return
					else:
						continue
				
				
				# Leaving events
				if event.eventtype() == 'quit' or event.eventtype() == 'part' and event.target().lower() == bridge.irc_room:
					if event.eventtype() == 'quit' and ( bridge.mode != 'normal' or isinstance(from_.irc_connection, irclib.ServerConnection) ):
						continue
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
					if bridge.irc_room == event.target().lower() and bridge.irc_server == connection.server:
						self.error(event_str, debug=True)
						message = event.arguments()[0]
						if event.eventtype() == 'action':
							message = '/me '+message
						from_.sayOnXMPP(message)
						return
					else:
						continue
			
			if handled == False:
				if not event.eventtype() in ['quit', 'part', 'nick', 'kick']:
					self.error(event_str, debug=True)
				self.error('=> Debug: event was not handled', debug=True)
			return
		
		
		# Handle bannedfromchan
		if event.eventtype() == 'bannedfromchan':
			if len(event.arguments()) < 1:
				self.error('=> Debug: length of arguments should be greater than 0 for a '+event.eventtype()+' event')
				return
			
			for bridge in self.bridges:
				if connection.server != bridge.irc_server or event.arguments()[0].lower() != bridge.irc_room:
					continue
				
				if event.target() == self.nickname:
					self.error('[Error] the nickname "'+event.target()+'" is banned from the IRC chan of bridge "'+str(bridge)+'"')
					raise Exception('[Error] the nickname "'+event.target()+'" is banned from the IRC chan of bridge "'+str(bridge)+'"')
				else:
					try:
						banned = bridge.getParticipant(event.target())
						if banned.irc_connection != 'bannedfromchan':
							banned.irc_connection = 'bannedfromchan'
							self.error(event_str, debug=True)
							self.error('[Notice] the nickname "'+event.target()+'" is banned from the IRC chan of bridge "'+str(bridge)+'"')
							bridge.say('[Warning] the nickname "'+event.target()+'" is banned from the IRC chan')
						else:
							self.error('=> Debug: ignoring '+event.eventtype(), debug=True)
					except NoSuchParticipantException:
						self.error('=> Debug: no such participant. WTF ?')
						return
			
			return
		
		
		# Joining events
		if event.eventtype() in ['namreply', 'join']:
			if connection.get_nickname() != self.nickname:
				self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
				return
			
			if event.eventtype() == 'namreply':
				# TODO: lock self.bridges for thread safety
				for bridge in self.getBridges(irc_room=event.arguments()[1].lower(), irc_server=connection.server):
					for nickname in re.split('(?:^[&@\+%]?|(?: [&@\+%]?)*)', event.arguments()[2].strip()):
						if nickname == '' or nickname == self.nickname:
							continue
						bridge.addParticipant('irc', nickname)
				return
			elif event.eventtype() == 'join':
				bridges = self.getBridges(irc_room=event.target().lower(), irc_server=connection.server)
				if len(bridges) == 0:
					self.error(event_str, debug=True)
					self.error('===> Debug: no bridge found for "'+event.target().lower()+' at '+connection.server+'"', debug=True)
					return
				for bridge in bridges:
					bridge.addParticipant('irc', nickname)
				return
		
		
		# From here the event is shown
		self.error(event_str, debug=True)
		
		
		if event.eventtype() in ['disconnect', 'kill']:
			if len(event.arguments()) > 0 and event.arguments()[0] == 'Connection reset by peer':
				return
			
			# TODO: lock self.bridges for thread safety
			for bridge in self.bridges:
				if connection.server != bridge.irc_server:
					continue
				try:
					p = bridge.getParticipant(connection.get_nickname())
					if bridge.mode == 'normal':
						bridge.switchFromNormalToLimitedMode()
					else:
						if p.irc_connection.really_connected == True:
							p.irc_connection.part(bridge.irc_room, message=message)
						p.irc_connection.used_by -= 1
						if p.irc_connection.used_by < 1:
							p.irc_connection.close(message)
						p.irc_connection = None
				except NoSuchParticipantException:
					pass
			return
		
		
		# Nickname callbacks
		# TODO: move this into irclib.py
		if event.eventtype() == 'nicknameinuse':
			connection._call_nick_callbacks('nicknameinuse')
			return
		if event.eventtype() == 'nickcollision':
			connection._call_nick_callbacks('nickcollision')
			return
		if event.eventtype() == 'erroneusnickname':
			connection._call_nick_callbacks('erroneusnickname')
			return
		
		
		# Unhandled events
		self.error('=> Debug: event not handled', debug=True)
	
	
	def _send_message_to_admins(self, message):
		"""[Internal] Send XMPP Message to bot admin(s)"""
		for admin_jid in self.admins_jid:
			try:
				self.xmpp_c.send(xmpp.protocol.Message(to=admin_jid, body=message, typ='chat'))
			except:
				pass
	
	
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
		c.mucs = []
		c.pings = []
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
		c.lock.acquire()
		c.used_by -= 1
		if c.used_by < 1:
			self.error('===> Debug: closing XMPP connection for "'+nickname+'"', debug=True)
			self.xmpp_connections.pop(nickname)
			c.send(xmpp.protocol.Presence(typ='unavailable'))
			c.lock.release()
			del c
		else:
			c.lock.release()
			self.error('===> Debug: XMPP connection for "'+nickname+'" is now used by '+str(c.used_by)+' bridges', debug=True)
	
	
	def removeBridge(self, bridge):
		self.bridges.remove(bridge)
		bridge.__del__()
	
	
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
