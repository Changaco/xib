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


import re
import shlex
import sys
import threading
from time import sleep
import traceback
import xml.parsers.expat

from argparse_modified import ArgumentParser
from encoding import *
import irclib
import muc
xmpp = muc.xmpp
del muc

from bridge import Bridge
from participant import Participant


class Bot(threading.Thread):
	
	commands = ['xmpp-participants', 'irc-participants', 'bridges']
	admin_commands = ['add-bridge', 'add-xmpp-admin', 'halt', 'remove-bridge', 'restart-bot', 'restart-bridge', 'stop-bridge']
	
	def __init__(self, jid, password, nickname, admins_jid=[], error_fd=sys.stderr, debug=False):
		threading.Thread.__init__(self)
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
		self.irc_thread = threading.Thread(target=self.irc.process_forever)
		self.irc_thread.start()
		# Open connection with XMPP server
		try:
			self.xmpp_c = self.get_xmpp_connection(self.nickname)
		except:
			self.error('[Error] XMPP Connection failed')
			raise
		self.xmpp_thread = threading.Thread(target=self._xmpp_loop)
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
				for j, c in enumerate(self.xmpp_connections.itervalues()):
					i += 1
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
				self.reopen_xmpp_connection(c)
				unlock = True
			except xmpp.Conflict:
				self.error('=> Debug: conflict', debug=True)
				self.reopen_xmpp_connection(c)
				unlock = True
			except:
				error = '[Error] Unknown exception on XMPP thread:\n'
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
			if bare_jid == bridge.xmpp_room_jid:
				# presence comes from a muc
				resource = unicode(from_.getResource())
				
				if resource == '':
					# presence comes from the muc itself
					pass
				
				elif resource == xmpp_c.nickname:
					# presence comes from self
					x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
					if x:
						d = x.getTag('destroy')
						if d:
							# room was destroyed
							# problem is that this is used by some MUC servers when they shut down or restart
							# considering this lack of semantic we have no choice but to do a check on the reason
							reason = d.getTag('reason')
							if reason:
								r = reason.getData()
								if r == 'The conference component is shutting down':
									# MUC server is going down, try to restart the bridges in 1 minute
									bridges = self.findBridges([from_.getDomain()])
									error_message = '[Warning] The MUC server '+from_.getDomain()+' seems to be going down, the bot will try to recreate all bridges related to this server in 1 minute'
									self.restart_bridges_delayed(bridges, 60, error_message)
									self.error(presence.__str__(fancy=1).encode('utf-8'), debug=True)
									return
								elif r == '':
									r = 'None given'
							else:
								r = 'None given'
							
							# room has been destroyed, stop the bridge
							self.error('[Error] The MUC room of the bridge '+str(bridge)+' has been destroyed with reason "'+r+'", stopping the bridge', send_to_admins=True)
							bridge.stop(message='The MUC room of the bridge has been destroyed with reason "'+r+'", stopping the bridge')
				
				else:
					# presence comes from a participant of the muc
					
					x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
					item = None
					if x:
						item = x.getTag('item')
					
					if presence.getType() == 'unavailable':
						try:
							p = bridge.getParticipant(resource)
						except Bridge.NoSuchParticipantException:
							p = None
						
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
							if p != None:
								bridge.removeParticipant('xmpp', resource, presence.getStatus())
					
					elif presence.getType() == 'error':
						error = presence.getTag('error')
						if error:
							for c in error.getChildren():
								if c.getNamespace() == 'urn:ietf:params:xml:ns:xmpp-stanzas' and c.getName() != 'text':
									err = error.getAttr('type')+' '+c.getName()
									if err == 'cancel remote-server-not-found':
										# Remote server not found
										# Stop bridges that depend on this server
										bridges = self.findBridges([from_.getDomain()])
										error_message = '[Error] XMPP Remote server not found: '+from_.getDomain()
										self.restart_bridges_delayed(bridges, 60, error_message)
										self.error(presence.__str__(fancy=1).encode('utf-8'), debug=True)
									else:
										raise Exception(presence.__str__(fancy=1).encode('utf-8'))
					
					elif resource != bridge.bot.nickname:
						real_jid = None
						if item and item.has_attr('jid'):
							real_jid = item.getAttr('jid')
						
						p = bridge.addParticipant('xmpp', resource, real_jid)
						
						# if we have the real jid check if the participant is a bot admin
						if real_jid and isinstance(p, Participant):
							for jid in self.admins_jid:
								if xmpp.protocol.JID(jid).bareMatch(real_jid):
									p.bot_admin = True
									break
						
						return
					
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
			from_bare_jid = unicode(message.getFrom().getNode()+'@'+message.getFrom().getDomain())
			for bridge in self.bridges:
				if from_bare_jid == bridge.xmpp_room_jid:
					# message comes from a room participant
					
					self.error('==> Debug: Received XMPP chat message.', debug=True)
					self.error(message.__str__(fancy=1), debug=True)
					
					try:
						from_ = bridge.getParticipant(message.getFrom().getResource())
						to_ = bridge.getParticipant(xmpp_c.nickname)
						
						from_.sayOnIRCTo(to_.nickname, message.getBody())
						
					except Bridge.NoSuchParticipantException:
						if xmpp_c.nickname == self.nickname:
							r = self.respond(str(message.getBody()), participant=from_)
							if isinstance(r, basestring) and len(r) > 0:
								s = xmpp.protocol.Message(to=message.getFrom(), body=r, typ='chat')
								self.error('==> Debug: Sending', debug=True)
								self.error(s.__str__(fancy=1), debug=True)
								xmpp_c.send(s)
							else:
								self.error('=> Debug: won\'t answer.', debug=True)
							return
						self.error('=> Debug: XMPP chat message not relayed', debug=True)
						return
			
			# message does not come from a room
			if xmpp_c.nickname == self.nickname:
				self.error('==> Debug: Received XMPP chat message.', debug=True)
				self.error(message.__str__(fancy=1), debug=True)
				
				# Find out if the message comes from a bot admin
				bot_admin = False
				for jid in self.admins_jid:
					if xmpp.protocol.JID(jid).bareMatch(message.getFrom()):
						bot_admin = True
						break
				
				# Respond
				r = self.respond(str(message.getBody()), bot_admin=bot_admin)
				if isinstance(r, basestring) and len(r) > 0:
					s = xmpp.protocol.Message(to=message.getFrom(), body=r, typ='chat')
					self.error('==> Debug: Sending', debug=True)
					self.error(s.__str__(fancy=1), debug=True)
					xmpp_c.send(s)
			
			else:
				self.error('=> Debug: Ignoring XMPP chat message not received on bot connection.', debug=True)
		
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
				if room_jid == bridge.xmpp_room_jid:
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
							participant = bridge.getParticipant(resource)
						except Bridge.NoSuchParticipantException:
							if resource != self.nickname:
								self.error('=> Debug: NoSuchParticipantException "'+resource+'" on "'+str(bridge)+'", WTF ?', debug=True)
							return
						
						participant.sayOnIRC(message.getBody())
						return
		
		elif message.getType() == 'error':
			for b in self.bridges:
				if message.getFrom() == b.xmpp_room_jid:
					# message comes from a room
					for c in message.getChildren():
						if c.getName() == 'error':
							for cc in c.getChildren():
								if cc.getNamespace() == 'urn:ietf:params:xml:ns:xmpp-stanzas' and cc.getName() != 'text':
									err = cc.getName()
									if err == 'not-acceptable':
										# we sent a message to a room we are not in
										# probable cause is a MUC server restart
										# let's restart the bot
										self.restart()
									elif err == 'forbidden':
										# we don't have the permission to speak
										# let's remove the bridge and tell admins
										self.error('[Error] Not allowed to speak on the XMPP MUC of bridge '+str(b)+', stopping it', send_to_admins=True)
										b.stop(message='Not allowed to speak on the XMPP MUC, stopping bridge.')
									else:
										self.error('==> Debug: recevied unknown error message', debug=True)
										self.error(message.__str__(fancy=1), debug=True)
					return
			
			self.error('==> Debug: recevied unknown error message', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
		
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
					
				except Bridge.NoSuchParticipantException:
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
						
					except Bridge.NoSuchParticipantException:
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
						except Bridge.NoSuchParticipantException:
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
					except Bridge.NoSuchParticipantException:
						self.error('=> Debug: no such participant. WTF ?')
						return
			
			return
		
		
		# Joining events
		if event.eventtype() in ['namreply', 'join']:
			if connection.get_nickname() != self.nickname:
				self.error('=> Debug: ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
				return
			
			if event.eventtype() == 'namreply':
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
					bridge.addParticipant('irc', nickname, irc_id=event.source())
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
					if bridge.mode in ['normal', 'bypass']:
						bridge.changeMode('limited')
					else:
						if p.irc_connection.really_connected == True:
							p.irc_connection.part(bridge.irc_room, message=message)
						p.irc_connection.used_by -= 1
						if p.irc_connection.used_by < 1:
							p.irc_connection.close(message)
						p.irc_connection = None
				except Bridge.NoSuchParticipantException:
					pass
			return
		
		
		# Nickname callbacks
		# TODO: move this into irclib.py
		if event.eventtype() == 'nicknameinuse':
			connection._call_nick_callbacks('nicknameinuse', arguments=[event])
			return
		if event.eventtype() == 'nickcollision':
			connection._call_nick_callbacks('nickcollision', arguments=[event])
			return
		if event.eventtype() == 'erroneusnickname':
			connection._call_nick_callbacks('erroneusnickname', arguments=[event])
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
		b = Bridge(self, xmpp_room, irc_room, irc_server, mode, say_level, irc_port=irc_port)
		self.bridges.append(b)
		return b
	
	
	def findBridges(self, str_array):
		# TODO: lock self.bridges for thread safety
		bridges = [b for b in self.bridges]
		for bridge in [b for b in bridges]:
			for s in str_array:
				if not s in str(bridge):
					bridges.remove(bridge)
					break
		return bridges
	
	
	def getBridges(self, irc_room=None, irc_server=None, xmpp_room_jid=None):
		# TODO: lock self.bridges for thread safety
		bridges = [b for b in self.bridges]
		for bridge in [b for b in bridges]:
			if irc_room != None and bridge.irc_room != irc_room:
				bridges.remove(bridge)
				continue
			if irc_server != None and bridge.irc_server != irc_server:
				bridges.remove(bridge)
				continue
			if xmpp_room_jid != None and bridge.xmpp_room_jid != xmpp_room_jid:
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
		if nickname == self.nickname:
			c.send(xmpp.protocol.Presence(priority=127))
		c.lock.release()
		return c
	
	
	def reopen_xmpp_connection(self, c):
		if not isinstance(c, xmpp.client.Client):
			return
		bot_connection = False
		if c == self.xmpp_c:
			bot_connection = True
		mucs = c.mucs
		nickname = c.nickname
		used_by = c.used_by
		participants = []
		for b in self.bridges:
			for p in b.participants:
				if p.xmpp_c == c:
					participants.append(p)
					p.xmpp_c = None
		self.error('===> Debug: reopening XMPP connection for "'+nickname+'"', debug=True)
		if self.xmpp_connections.has_key(nickname):
			self.xmpp_connections.pop(nickname)
		c.send(xmpp.protocol.Presence(typ='unavailable'))
		del c
		c = self.get_xmpp_connection(nickname)
		c.used_by = used_by
		if bot_connection:
			self.xmpp_c = c
		for p in participants:
			p.xmpp_c = c
		c.mucs = mucs
		for m in c.mucs:
			m.rejoin()
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
	
	
	def removeBridge(self, bridge, message='Removing bridge'):
		self.bridges.remove(bridge)
		bridge.stop(message)
	
	
	def respond(self, message, participant=None, bot_admin=False):
		ret = ''
		command = shlex.split(message)
		args_array = []
		if len(command) > 1:
			args_array = command[1:]
		command = command[0]
		
		if isinstance(participant, Participant) and bot_admin != participant.bot_admin:
			bot_admin = participant.bot_admin
		
		if command == 'xmpp-participants':
			if not isinstance(participant, Participant):
				for b in self.bridges:
					xmpp_participants_nicknames = b.get_participants_nicknames_list(protocols=['xmpp'])
					ret += '\nparticipants on '+b.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
				return ret
			else:
				xmpp_participants_nicknames = participant.bridge.get_participants_nicknames_list(protocols=['xmpp'])
				return '\nparticipants on '+participant.bridge.xmpp_room_jid+' ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
		
		elif command == 'irc-participants':
			if not isinstance(participant, Participant):
				for b in self.bridges:
					irc_participants_nicknames = b.get_participants_nicknames_list(protocols=['irc'])
					ret += '\nparticipants on '+b.irc_room+' at '+b.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
				return ret
			else:
				irc_participants_nicknames = participant.bridge.get_participants_nicknames_list(protocols=['irc'])
				return '\nparticipants on '+participant.bridge.irc_room+' at '+participant.bridge.irc_server+' ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
		
		elif command == 'bridges':
			parser = ArgumentParser(prog=command)
			parser.add_argument('--show-mode', default=False, action='store_true')
			parser.add_argument('--show-say-level', default=False, action='store_true')
			parser.add_argument('--show-participants', default=False, action='store_true')
			try:
				args = parser.parse_args(args_array)
			except ArgumentParser.ParseException as e:
				return '\n'+e.args[1]
			ret = 'List of bridges:'
			for i, b in enumerate(self.bridges):
				ret += '\n'+str(i+1)+' - '+str(b)
				if args.show_mode:
					ret += ' - mode='+b.mode
				if args.show_say_level:
					ret += ' - say_level='+bridge._say_levels[b.say_level]
				if args.show_participants:
					xmpp_participants_nicknames = b.get_participants_nicknames_list(protocols=['xmpp'])
					ret += '\nparticipants on XMPP ('+str(len(xmpp_participants_nicknames))+'): '+' '.join(xmpp_participants_nicknames)
					irc_participants_nicknames = b.get_participants_nicknames_list(protocols=['irc'])
					ret += '\nparticipants on IRC ('+str(len(irc_participants_nicknames))+'): '+' '.join(irc_participants_nicknames)
				if b.irc_connection == None:
					ret += ' - this bridge is stopped, use "restart-bridge '+str(i+1)+'" to restart it'
			return ret
		
		elif command in Bot.admin_commands:
			if bot_admin == False:
				return 'You have to be a bot admin to use this command.'
			
			if command == 'add-bridge':
				parser = ArgumentParser(prog=command)
				parser.add_argument('xmpp_room_jid', type=str)
				parser.add_argument('irc_chan', type=str)
				parser.add_argument('irc_server', type=str)
				parser.add_argument('--mode', choices=bridge._modes, default='normal')
				parser.add_argument('--say-level', choices=bridge._say_levels, default='all')
				parser.add_argument('--irc-port', type=int, default=6667)
				try:
					args = parser.parse_args(args_array)
				except ArgumentParser.ParseException as e:
					return '\n'+e.args[1]
				
				self.new_bridge(args.xmpp_room_jid, args.irc_chan, args.irc_server, args.mode, args.say_level, irc_port=args.irc_port)
				
				return 'Bridge added.'
			
			elif command == 'add-xmpp-admin':
				parser = ArgumentParser(prog=command)
				parser.add_argument('jid', type=str)
				try:
					args = parser.parse_args(args_array)
				except ArgumentParser.ParseException as e:
					return '\n'+e.args[1]
				self.admins_jid.append(args.jid)
				for b in self.bridges:
					for p in b.participants:
						if p.real_jid != None and xmpp.protocol.JID(args.jid).bareMatch(p.real_jid):
							p.bot_admin = True
				
				return 'XMPP admin added.'
				
			elif command == 'restart-bot':
				self.restart()
				return
			elif command == 'halt':
				self.__del__()
				return
			
			
			elif command in ['remove-bridge', 'restart-bridge', 'stop-bridge']:
				# we need to know which bridge the command is for
				if len(args_array) == 0:
					if isinstance(participant, Participant):
						b = participant.bridge
					else:
						return 'You must specify a bridge. '+self.respond('bridges')
				else:
					try:
						bn = int(args_array[0])
						if bn < 1:
							raise IndexError
						b = self.bridges[bn-1]
					except IndexError:
						return 'Invalid bridge number "'+str(bn)+'". '+self.respond('bridges')
					except ValueError:
						bridges = self.findBridges(args_array)
						if len(bridges) == 0:
							return 'No bridge found matching "'+' '.join(args_array)+'". '+self.respond('bridges')
						elif len(bridges) == 1:
							b = bridges[0]
						elif len(bridges) > 1:
							return 'More than one bridge matches "'+' '.join(args_array)+'", please be more specific. '+self.respond('bridges')
					
				if command == 'remove-bridge':
					self.removeBridge(b)
					return 'Bridge removed.'
				elif command == 'restart-bridge':
					b.restart()
					return 'Bridge restarted.'
				elif command == 'stop-bridge':
					b.stop()
					return 'Bridge stopped.'
		
		else:
			ret = 'Error: "'+command+'" is not a valid command.\ncommands:  '+'  '.join(Bot.commands)
			if bot_admin == True:
				return ret+'\n'+'admin commands:  '+'  '.join(Bot.admin_commands)
			else:
				return ret
	
	
	def restart(self):
		# Stop the bridges
		for b in self.bridges:
			b.stop(message='Restarting bot')
		
		# Reopen the bot's XMPP connection
		self.reopen_xmpp_connection(self.xmpp_c)
		
		# Restart the bridges
		for b in self.bridges:
			b.init2()
		
		self.error('Bot restarted.', send_to_admins=True)
	
	
	def restart_bridges_delayed(self, bridges, delay, error_message):
		if len(bridges) > 0:
			error_message += '\nThese bridges will be stopped:'
			for b in bridges:
				error_message += '\n'+str(b)
				if hasattr(b, 'reconnecting'):
					leave_message = 'MUC server seems to be down'
				else:
					leave_message = 'MUC server seems to be down, will try to recreate the bridge in '+str(delay)+' seconds'
					self.reconnecting = True
					self.irc.execute_delayed(delay, b.init2)
				b.stop(message=leave_message)
		
		self.error(error_message, send_to_admins=True)
	
	
	def __del__(self):
		for bridge in self.bridges:
			self.removeBridge(bridge)
