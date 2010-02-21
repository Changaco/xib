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
import sys
import threading
from time import sleep
import traceback
import xml.parsers.expat

import irclib
import muc
xmpp = muc.xmpp
del muc

from bridge import Bridge
from participant import Participant
import commands
import say_levels


class Bot(threading.Thread):
	
	def __init__(self, jid, password, nickname, admins=[], error_fd=sys.stderr, debug=False):
		threading.Thread.__init__(self)
		self.halt = False
		self.bridges = []
		self.bare_jid = xmpp.protocol.JID(jid=jid)
		self.bare_jid.setResource('')
		self.nickname = nickname
		self.password = password
		self.error_fd = error_fd
		self.debug = debug
		self.admins = admins
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
			self.error(say_levels.error, 'XMPP Connection failed')
			raise
		self.xmpp_thread = threading.Thread(target=self._xmpp_loop)
		self.xmpp_thread.start()
	
	
	def error(self, importance, message, debug=False, send_to_admins=False):
		"""Output an error message."""
		if send_to_admins == True:
			self._send_message_to_admins(importance, message)
		if importance == -1:
			return
		if not debug:
			self.error_fd.write(self.format_message(importance, message).encode('utf-8')+'\n')
		if debug and self.debug:
			self.error_fd.write('='*importance+'> '+message.encode('utf-8')+'\n')
	
	
	def _xmpp_loop(self):
		"""[Internal] XMPP infinite loop."""
		i = 1
		while True:
			if self.halt:
				s = len(self.xmpp_connections)
				for i in range(s):
					self.close_xmpp_connection(self.xmpp_connections.keys()[s-i-1], force=True)
				break
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
							self.error(1, 'sending XMPP ping', debug=True)
							c.pings.append(c.send(ping))
						if hasattr(c, 'Process'):
							c.Process(0.01)
						c.lock.release()
					if i > 5000:
						i = 0
			except RuntimeError:
				pass
			except (xml.parsers.expat.ExpatError, xmpp.protocol.XMLNotWellFormed):
				self.error(1, 'invalid stanza', debug=True)
				self.reopen_xmpp_connection(c)
				unlock = True
			except xmpp.Conflict:
				self.error(1, 'conflict', debug=True)
				self.reopen_xmpp_connection(c)
				unlock = True
			except:
				error = 'Unknown exception on XMPP thread:\n'+traceback.format_exc()
				self.error(say_levels.error, error, send_to_admins=True)
				unlock = True
			if unlock == True:
				c.lock.release()
	
	
	def _xmpp_presence_handler(self, dispatcher, presence):
		"""[Internal] Manage XMPP presence."""
		
		xmpp_c = dispatcher._owner
		
		if xmpp_c.nickname != self.nickname:
			self.error(1, 'Skipping XMPP presence not received on bot connection.', debug=True)
			return
		
		self.error(2, 'Received XMPP presence.\n'+presence.__str__(fancy=1), debug=True)
		
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
									m = 'The MUC server '+from_.getDomain()+' seems to be going down, the bot will try to recreate all bridges related to this server in 1 minute'
									error = [say_levels.warning, m]
									self.restart_bridges_delayed(bridges, 60, error)
									return
								elif r == '':
									r = 'None given'
							else:
								r = 'None given'
							
							# room has been destroyed, stop the bridge
							self.error(say_levels.error, 'The MUC room of the bridge '+str(bridge)+' has been destroyed with reason "'+r+'", stopping the bridge', send_to_admins=True)
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
								self.error(1, 'bad stanza, no item element', debug=True)
								return
							new_nick = item.getAttr('nick')
							if not new_nick:
								self.error(1, 'bad stanza, new nick is not given', debug=True)
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
								bridge.say(say_levels.error, 'bot got banned from XMPP', on_xmpp=False, send_to_admins=True)
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
										error = [say_levels.error, 'XMPP Remote server not found: '+from_.getDomain()]
										self.restart_bridges_delayed(bridges, 60, error)
									else:
										raise Exception(presence.__str__(fancy=1).encode('utf-8'))
					
					elif resource != bridge.bot.nickname:
						real_jid = None
						if item and item.has_attr('jid'):
							real_jid = item.getAttr('jid')
						
						p = bridge.addParticipant('xmpp', resource, real_jid)
						
						# if we have the real jid check if the participant is a bot admin
						if real_jid and isinstance(p, Participant):
							for admin in self.admins:
								if xmpp.protocol.JID(admin.jid).bareMatch(real_jid):
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
			self.error(1, 'received XMPP pong', debug=True)
			return
		
		self.error(2, 'Received XMPP iq.\n'+iq.__str__(fancy=1), debug=True)
	
	
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
					
					self.error(2, 'Received XMPP chat message.\n'+message.__str__(fancy=1), debug=True)
					
					try:
						from_ = bridge.getParticipant(message.getFrom().getResource())
						to_ = bridge.getParticipant(xmpp_c.nickname)
						
						from_.sayOnIRCTo(to_.nickname, message.getBody())
						
					except Bridge.NoSuchParticipantException:
						if xmpp_c.nickname == self.nickname:
							r = self.respond(str(message.getBody()), participant=from_)
							if isinstance(r, basestring) and len(r) > 0:
								s = xmpp.protocol.Message(to=message.getFrom(), body=r, typ='chat')
								self.error(2, 'Sending\n'+s.__str__(fancy=1), debug=True)
								xmpp_c.send(s)
							else:
								self.error(1, 'won\'t answer.', debug=True)
							return
						self.error(1, 'XMPP chat message not relayed', debug=True)
						return
			
			# message does not come from a room
			if xmpp_c.nickname == self.nickname:
				self.error(2, 'Received XMPP chat message.\n'+message.__str__(fancy=1), debug=True)
				
				# Find out if the message comes from a bot admin
				bot_admin = False
				for admin in self.admins:
					if xmpp.protocol.JID(admin.jid).bareMatch(message.getFrom()):
						bot_admin = True
						break
				
				# Respond
				r = self.respond(str(message.getBody()), bot_admin=bot_admin)
				if isinstance(r, basestring) and len(r) > 0:
					s = xmpp.protocol.Message(to=message.getFrom(), body=r, typ='chat')
					self.error(2, 'Sending\n'+s.__str__(fancy=1), debug=True)
					xmpp_c.send(s)
			
			else:
				self.error(1, 'Ignoring XMPP chat message not received on bot connection.', debug=True)
		
		elif message.getType() == 'groupchat':
			# message comes from a room
			
			for child in message.getChildren():
				if child.getName() == 'delay':
					# MUC delayed message
					return
			
			if xmpp_c.nickname != self.nickname:
				self.error(1, 'Ignoring XMPP MUC message not received on bot connection.', debug=True)
				return
			
			
			from_ = xmpp.protocol.JID(message.getFrom())
			
			if unicode(from_.getResource()) == self.nickname:
				self.error(1, 'Ignoring XMPP MUC message sent by self.', debug=True)
				return
			
			room_jid = unicode(from_.getNode()+'@'+from_.getDomain())
			for bridge in self.bridges:
				if room_jid == bridge.xmpp_room_jid:
					resource = unicode(from_.getResource())
					if resource == '':
						# message comes from the room itself
						self.error(1, 'Ignoring XMPP groupchat message sent by the room.', debug=True)
						return
					else:
						# message comes from a participant of the room
						self.error(2, 'Received XMPP groupchat message.\n'+message.__str__(fancy=1), debug=True)
						
						try:
							participant = bridge.getParticipant(resource)
						except Bridge.NoSuchParticipantException:
							if resource != self.nickname:
								self.error(1, 'NoSuchParticipantException "'+resource+'" on "'+str(bridge)+'", WTF ?', debug=True)
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
										self.error(say_levels.error, 'Not allowed to speak on the XMPP MUC of bridge '+str(b)+', stopping it', send_to_admins=True)
										b.stop(message='Not allowed to speak on the XMPP MUC, stopping bridge.')
									else:
										self.error(2, 'recevied unknown error message\n'+message.__str__(fancy=1), debug=True)
					return
			
			self.error(2, 'recevied unknown error message\n'+message.__str__(fancy=1), debug=True)
		
		else:
			self.error(2, 'Received XMPP message of unknown type "'+str(message.getType())+'".\n'+message.__str__(fancy=1), debug=True)
	
	
	def _irc_event_handler(self, connection, event):
		"""[Internal] Manage IRC events"""
		
		# Answer ping
		if event.eventtype() == 'ping':
			connection.pong(connection.get_server_name())
			return
		
		
		# Events we always want to ignore
		if 'all' in event.eventtype() or 'motd' in event.eventtype() or event.eventtype() in ['nicknameinuse', 'nickcollision', 'erroneusnickname']:
			return
		if event.eventtype() in ['pong', 'privnotice', 'ctcp', 'nochanmodes', 'notexttosend', 'currenttopic', 'topicinfo', '328', 'pubnotice', '042']:
			self.error(1, 'ignoring IRC '+event.eventtype(), debug=True)
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
					self.error(3, 'event target ('+event.target()+') and connection nickname ('+connection.nickname+') don\'t match', debug=True)
					connection._call_nick_callbacks('nicknametoolong', arguments=[len(event.target())])
			self.error(1, 'ignoring '+event.eventtype(), debug=True)
			return
		
		
		# A string representation of the event
		event_str = 'connection='+connection.__str__()+'\neventtype='+event.eventtype()+'\nsource='+repr(event.source())+'\ntarget='+repr(event.target())+'\narguments='+repr(event.arguments())
		debug_str = 'Received IRC event.\n'+event_str
		printed_event = False
		
		
		if event.eventtype() in ['pubmsg', 'action', 'privmsg', 'quit', 'part', 'nick', 'kick']:
			if nickname == None:
				return
			
			handled = False
			
			if event.eventtype() in ['quit', 'part'] and nickname == self.nickname:
				return
			
			if event.eventtype() in ['quit', 'part', 'nick', 'kick']:
				if connection.get_nickname() != self.nickname:
					self.error(1, 'ignoring IRC '+event.eventtype()+' not received on bot connection', debug=True)
					return
				else:
					self.error(2, debug_str, debug=True)
					printed_event = True
			
			if event.eventtype() == 'kick' and len(event.arguments()) < 1:
				self.error(1, 'at least 1 argument is needed for a '+event.eventtype()+' event', debug=True)
				return
			
			if event.eventtype() in ['pubmsg', 'action']:
				if connection.get_nickname() != self.nickname:
					self.error(1, 'ignoring IRC '+event.eventtype()+' not received on bot connection', debug=True)
					return
				if nickname == self.nickname:
					self.error(1, 'ignoring IRC '+event.eventtype()+' sent by self', debug=True)
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
						self.error(2, debug_str, debug=True)
						from_.sayOnXMPPTo(to_.nickname, event.arguments()[0])
						return
						
					except Bridge.NoSuchParticipantException:
						if event.target().split('!')[0] == self.nickname:
							# Message is for the bot
							self.error(2, debug_str, debug=True)
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
							self.error(1, 'a participant that was not here has been kicked ? WTF ?', debug=True)
							return
					else:
						continue
				
				
				# Leaving events
				if event.eventtype() == 'quit' or event.eventtype() == 'part' and event.target().lower() == bridge.irc_room:
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
						self.error(2, debug_str, debug=True)
						message = event.arguments()[0]
						if event.eventtype() == 'action':
							message = '/me '+message
						from_.sayOnXMPP(message)
						return
					else:
						continue
			
			if handled:
				return
		
		
		# Handle bannedfromchan
		if event.eventtype() == 'bannedfromchan':
			if len(event.arguments()) < 1:
				self.error(1, 'length of arguments should be greater than 0 for a '+event.eventtype()+' event', debug=True)
				return
			
			for bridge in self.bridges:
				if connection.server != bridge.irc_server or event.arguments()[0].lower() != bridge.irc_room:
					continue
				
				if event.target() == self.nickname:
					self.error(say_levels.error, 'the nickname "'+event.target()+'" is banned from the IRC chan of bridge "'+str(bridge)+'"')
					raise Exception('[Error] the nickname "'+event.target()+'" is banned from the IRC chan of bridge "'+str(bridge)+'"')
				else:
					try:
						banned = bridge.getParticipant(event.target())
						if banned.irc_connection != 'bannedfromchan':
							banned.irc_connection = 'bannedfromchan'
							self.error(2, debug_str, debug=True)
							bridge.say(say_levels.warning, 'the nickname "'+event.target()+'" is banned from the IRC chan', log=True)
						else:
							self.error(1, 'ignoring '+event.eventtype(), debug=True)
					except Bridge.NoSuchParticipantException:
						self.error(1, 'no such participant. WTF ?', debug=True)
						return
			
			return
		
		
		if event.eventtype() in ['disconnect', 'kill', 'error']:
			if len(event.arguments()) > 0 and event.arguments()[0] == 'Connection reset by peer':
				self.error(2, debug_str, debug=True)
			else:
				self.error(say_levels.debug, debug_str, send_to_admins=True)
			return
		
		
		if event.eventtype() in ['cannotsendtochan', 'notonchannel']:
			self.error(2, debug_str, debug=True)
			bridges = self.getBridges(irc_room=event.arguments()[0], irc_server=connection.server)
			if len(bridges) > 1:
				raise Exception, 'more than one bridge for one irc chan, WTF ?'
			bridge = bridges[0]
			if connection.get_nickname() == self.nickname:
				bridge._join_irc_failed()
			else:
				p = bridge.getParticipant(connection.get_nickname())
				p._close_irc_connection('')
				p.irc_connection = error
			return
		
		
		# Ignore events not received on bot connection
		if connection.get_nickname() != self.nickname:
			self.error(1, 'ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
			return
		
		
		# Joining events
		if event.eventtype() in ['namreply', 'join']:
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
					self.error(2, debug_str, debug=True)
					self.error(3, 'no bridge found for "'+event.target().lower()+' at '+connection.server+'"', debug=True)
					return
				for bridge in bridges:
					bridge.addParticipant('irc', nickname, irc_id=event.source())
				return
		
		
		# Mode event
		if event.eventtype() == 'mode':
			if len(event.arguments()) < 2:
				self.error(2, debug_str, debug=True)
				self.error(1, '2 arguments are needed for a '+event.eventtype()+' event', debug=True)
				return
			if event.arguments()[1] != self.nickname or not 'o' in event.arguments()[0]:
				self.error(1, 'ignoring IRC mode "'+event.arguments()[0]+'" for "'+event.arguments()[1]+'"', debug=True)
				return
			self.error(2, debug_str, debug=True)
			bridges = self.getBridges(irc_room=event.target(), irc_server=connection.server)
			if len(bridges) > 1:
				raise Exception, 'more than one bridge for one irc chan, WTF ?'
			bridge = bridges[0]
			if re.search('\+[^\-]*o', event.arguments()[0]):
				# bot is channel operator
				bridge.irc_op = True
				self.error(say_levels.notice, 'bot has IRC operator privileges in '+event.target())
			elif re.search('\-[^\+]*o', event.arguments()[0]):
				# bot lost channel operator privileges
				if bridge.irc_op:
					self.error(say_levels.notice, 'bot lost IRC operator privileges in '+event.target(), send_to_admins=True)
				bridge.irc_op = False
			return
		
		
		# Unhandled events
		if not printed_event:
			self.error(say_levels.debug, 'The following IRC event was not handled:\n'+event_str+'\n', send_to_admins=True)
		else:
			self.error(1, 'event not handled', debug=True)
			self._send_message_to_admins(say_levels.debug, 'The following IRC event was not handled:\n'+event_str)
	
	
	def _send_message_to_admins(self, importance, message):
		"""[Internal] Send XMPP Message to bot admin(s)"""
		for admin in self.admins:
			if importance != -1:
				if admin.say_level == say_levels.nothing or importance < admin.say_level:
					continue
				message = self.format_message(importance, message)
			
			try:
				self.xmpp_c.send(xmpp.protocol.Message(to=admin.jid, body=message, typ='chat'))
			except:
				pass
	
	
	def new_bridge(self, xmpp_room, irc_room, irc_server, mode, say_level, irc_port=6667, irc_connection_interval=1, irc_charsets=None):
		"""Create a bridge between xmpp_room and irc_room at irc_server."""
		b = Bridge(self, xmpp_room, irc_room, irc_server, mode, say_level, irc_port=irc_port, irc_connection_interval=irc_connection_interval, irc_charsets=irc_charsets)
		self.bridges.append(b)
		return b
	
	
	def findBridges(self, str_array):
		# TODO: lock self.bridges for thread safety
		bridges = [b for b in self.bridges]
		for bridge in self.bridges:
			for s in str_array:
				if not s in str(bridge):
					bridges.remove(bridge)
					break
		return bridges
	
	
	def format_message(self, importance, message):
		if importance < 0 or importance >= len(say_levels.levels):
			raise Exception('[Internal Error] unknown message importance')
		return'['+str(say_levels.get(importance))+'] '+message
	
	
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
			self.error(3, 'using existing XMPP connection for "'+nickname+'", now used by '+str(c.used_by)+' bridges', debug=True)
			return c
		self.error(3, 'opening new XMPP connection for "'+nickname+'"', debug=True)
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
		self.error(3, 'reopening XMPP connection for "'+nickname+'"', debug=True)
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
	
	
	def close_xmpp_connection(self, nickname, force=False):
		if not self.xmpp_connections.has_key(nickname):
			return
		c = self.xmpp_connections[nickname]
		c.lock.acquire()
		c.used_by -= 1
		if c.used_by < 1 or force:
			self.error(3, 'closing XMPP connection for "'+nickname+'"', debug=True)
			self.xmpp_connections.pop(nickname)
			c.send(xmpp.protocol.Presence(typ='unavailable'))
			c.lock.release()
			del c
		else:
			c.lock.release()
			self.error(3, 'XMPP connection for "'+nickname+'" is now used by '+str(c.used_by)+' bridges', debug=True)
	
	
	def removeBridge(self, bridge, message='Removing bridge'):
		self.bridges.remove(bridge)
		bridge.stop(message)
	
	
	def respond(self, message, participant=None, bot_admin=False):
		if isinstance(participant, Participant):
			bridge = participant.bridge
			if bot_admin != participant.bot_admin:
				bot_admin = participant.bot_admin
		else:
			bridge = None
		
		return commands.execute(self, message, bot_admin, bridge)
	
	
	def restart(self):
		# Stop the bridges
		for b in self.bridges:
			b.stop(message='Restarting bot')
		
		# Reopen the bot's XMPP connection
		self.reopen_xmpp_connection(self.xmpp_c)
		
		# Restart the bridges
		for b in self.bridges:
			b.init2()
		
		self.error(-1, 'Bot restarted.', send_to_admins=True)
	
	
	def restart_bridges_delayed(self, bridges, delay, error, protocol='xmpp'):
		if len(bridges) > 0:
			error[1] += '\nThese bridges will be stopped:'
			for b in bridges:
				error[1] += '\n'+str(b)
				
				if protocol == 'xmpp':
					leave_message = 'Could not connect to the MUC server ('+b.xmpp_room_jid+')'
				else:
					leave_message = 'Could not connect to the IRC server ('+b.irc_connection._server_str()+')'
				
				if not hasattr(b, 'reconnecting'):
					leave_message += 'will try to recreate the bridge in '+str(delay)+' seconds'
					b.reconnecting = True
					self.irc.execute_delayed(delay, b.init2)
				
				b.stop(message=leave_message)
		
		self.error(error[0], error[1], send_to_admins=True)
	
	
	def stop(self, message='Stopping bot'):
		for bridge in self.bridges:
			bridge.stop(message=message)
	
	
	def __del__(self):
		for bridge in self.bridges:
			self.removeBridge(bridge, message='Stopping bot')
		self.halt = True
