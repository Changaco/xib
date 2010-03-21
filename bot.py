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
	
	
	def error(self, importance, message, debug=False, no_debug_add='', send_to_admins=False):
		"""Output an error message."""
		try:
			message = message.encode('utf-8')
		except:
			message = repr(message)
		if not self.debug:
			 message += no_debug_add
		if send_to_admins == True:
			self._send_message_to_admins(importance, message)
		if importance == -1:
			return
		if not debug:
			self.error_fd.write(self.format_message(importance, message)+'\n')
		elif self.debug:
			self.error_fd.write('='*importance+'> '+message+'\n')
	
	
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
		
		from_ = xmpp.protocol.JID(presence.getFrom())
		bare_jid = unicode(from_.getNode()+'@'+from_.getDomain())
		
		if bare_jid == self.bare_jid:
			self.error(1, 'Ignoring XMPP presence from self', debug=True)
			return
		
		self.error(2, 'Received XMPP presence.\n'+presence.__str__(fancy=1), debug=True)
		
		no_debug_add = '\n'+presence.__str__(fancy=1)
		
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
									bridges = self.iter_bridges(patterns=[from_.getDomain()])
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
							bridge.stop(message='The MUC room has been destroyed with reason "'+r+'", stopping the bridge')
				
				else:
					# presence comes from a participant of the muc
					
					x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
					item = None
					if x:
						item = x.getTag('item')
					
					if presence.getType() == 'unavailable':
						try:
							p = bridge.get_participant(resource)
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
								self.error(say_levels.debug, 'bad stanza, no item element', no_debug_add=no_debug_add)
								return
							new_nick = item.getAttr('nick')
							if not new_nick:
								self.error(say_levels.debug, 'bad stanza, new nick is not given', no_debug_add=no_debug_add)
								return
							p.change_nickname(new_nick, 'irc')
							
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
								
								bridge.remove_participant('xmpp', p.nickname, s1+s2)
							
						elif x and x.getTag('status', attrs={'code': '301'}):
							# participant was banned
							if p == None:
								bridge.say(say_levels.error, 'bot got banned from XMPP', on_xmpp=False, send_to_admins=True)
								self.remove_bridge(bridge)
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
							
							bridge.remove_participant('xmpp', p.nickname, s1+s2)
							
						else:
							# participant left
							if p != None:
								bridge.remove_participant('xmpp', resource, presence.getStatus())
					
					elif presence.getType() == 'error':
						error = presence.getTag('error')
						if error:
							for c in error.getChildren():
								if c.getNamespace() == 'urn:ietf:params:xml:ns:xmpp-stanzas' and c.getName() != 'text':
									err = error.getAttr('type')+' '+c.getName()
									if err == 'cancel remote-server-not-found':
										# Remote server not found
										# Stop bridges that depend on this server
										bridges = self.iter_bridges(patterns=[from_.getDomain()])
										error = [say_levels.error, 'XMPP Remote server not found: '+from_.getDomain()]
										self.restart_bridges_delayed(bridges, 60, error)
									else:
										raise Exception(presence.__str__(fancy=1).encode('utf-8'))
					
					elif resource != bridge.bot.nickname:
						real_jid = None
						if item and item.has_attr('jid'):
							real_jid = item.getAttr('jid')
						
						p = bridge.add_participant('xmpp', resource, real_jid=real_jid)
						
						# if we have the real jid check if the participant is a bot admin
						if real_jid and isinstance(p, Participant):
							for admin in self.admins:
								if xmpp.protocol.JID(admin.jid).bareMatch(real_jid):
									p.bot_admin = True
									break
						
						return
					
				return
		
		self.error(say_levels.debug, 'Unhandled XMPP presence', no_debug_add='\n'+presence.__str__(fancy=1))
	
	
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
		
		
		# Private message
		if message.getType() == 'chat':
			from_bare_jid = unicode(message.getFrom().getNode()+'@'+message.getFrom().getDomain())
			
			try:
				bridge = self.get_bridge(xmpp_room_jid=from_bare_jid)
			except KeyError:
				bridge = None
			
			if bridge:
				# message comes from a room participant
				self.error(2, 'Received XMPP chat message.\n'+message.__str__(fancy=1), debug=True)
				
				try:
					from_ = bridge.get_participant(message.getFrom().getResource())
					to_ = bridge.get_participant(xmpp_c.nickname)
					
					from_.say_on_irc_to(to_.nickname, message.getBody())
					
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
					self.error(say_levels.debug, 'XMPP chat message not relayed', no_debug_add='\n'+message.__str__(fancy=1))
					return
			
			else:
				# message does not come from a room participant
				if xmpp_c.nickname != self.nickname:
					self.error(1, 'Ignoring XMPP chat message not received on bot connection.', debug=True)
					return
				
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
		
		
		# MUC message
		elif message.getType() == 'groupchat':
			
			for child in message.getChildren():
				if child.getName() == 'delay':
					# MUC delayed message
					return
			
			if xmpp_c.nickname != self.nickname:
				self.error(1, 'Ignoring XMPP MUC message not received on bot connection.', debug=True)
				return
			
			from_ = xmpp.protocol.JID(message.getFrom())
			
			resource = unicode(from_.getResource())
			
			if resource == self.nickname:
				self.error(1, 'Ignoring XMPP MUC message sent by self.', debug=True)
				return
			
			room_jid = unicode(from_.getNode()+'@'+from_.getDomain())
			bridge = self.get_bridge(xmpp_room_jid=room_jid)
			
			if resource == '':
				# message comes from the room itself
				self.error(1, 'Ignoring XMPP groupchat message sent by the room.', debug=True)
				return
			else:
				# message comes from a participant of the room
				self.error(2, 'Received XMPP groupchat message.\n'+message.__str__(fancy=1), debug=True)
				
				try:
					participant = bridge.get_participant(resource)
					participant.say_on_irc(message.getBody())
				except Bridge.NoSuchParticipantException:
					bridge.say_on_behalf(resource, message.getBody(), 'irc', action=(message.getBody()[:4] == '/me '))
				
				return
		
		
		# Error message
		elif message.getType() == 'error':
			try:
				b = self.get_bridge(xmpp_room_jid=message.getFrom())
			except KeyError:
				self.error(say_levels.debug, 'received unknown error message\n'+message.__str__(fancy=1))
				return
			
			for c in message.getChildren():
				if c.getName() == 'error':
					for cc in c.getChildren():
						if cc.getNamespace() == 'urn:ietf:params:xml:ns:xmpp-stanzas' and cc.getName() != 'text':
							err = cc.getName()
							if err in ['not-acceptable', 'not-allowed']:
								# we sent a message to a room we are not in
								# can be due to a MUC server restart
								# can be a concurrency bug
								if xmpp_c.nickname == self.nickname:
									b.restart(message='Automatic restart of bridge')
								else:
									try:
										p = b.get_participant(xmpp_c.nickname)
										p.say_on_XMPP_through_bridge(message.getBody())
									except Bridge.NoSuchParticipantException:
										b.restart(message='Automatic restart of bridge')
								
							elif err == 'forbidden':
								# we don't have the permission to speak
								# let's remove the bridge and tell admins
								self.error(say_levels.error, 'Not allowed to speak on the XMPP MUC of bridge '+str(b)+', stopping it', send_to_admins=True)
								b.stop(message='Not allowed to speak on the XMPP MUC, stopping the bridge')
							else:
								self.error(say_levels.debug, 'recevied unknown error message\n'+message.__str__(fancy=1))
			
			return
		
		
		# Unknown message type
		else:
			self.error(say_levels.debug, 'Received XMPP message of unknown type "'+str(message.getType())+'".\n'+message.__str__(fancy=1))
	
	
	def _irc_event_handler(self, connection, event):
		"""[Internal] Manage IRC events"""
		
		# Answer ping
		if event.eventtype() == 'ping':
			connection.pong(connection.get_server_name())
			return
		
		
		# Events we always want to ignore
		if 'all' in event.eventtype() or 'motd' in event.eventtype() or event.eventtype() in ['nicknameinuse', 'nickcollision', 'erroneusnickname']:
			return
		if event.eventtype() in ['pong', 'privnotice', 'ctcp', 'nochanmodes', 'notexttosend', 'currenttopic', 'topicinfo', '328', 'pubnotice', '042', 'umode', 'welcome', 'yourhost', 'created', 'myinfo', 'featurelist', 'luserclient', 'luserop', 'luserchannels', 'luserme', 'n_local', 'n_global', 'endofnames', 'luserunknown', 'luserconns', 'inviteonlychan', 'bannedfromchan', 'channelisfull', 'badchannelkey', 'topic', 'noorigin']:
			self.error(1, 'ignoring IRC '+event.eventtype(), debug=True)
			return
		
		
		source_nickname = None
		if event.source() and '!' in event.source():
			source_nickname = event.source().split('!')[0]
		
		
		# A string representation of the event
		event_str = '\nconnection='+connection.__str__()+'\neventtype='+event.eventtype()+'\nsource='+repr(event.source())+'\ntarget='+repr(event.target())+'\narguments='+repr(event.arguments())
		debug_str = 'Received IRC event.'+event_str
		
		handled = False
		
		# Private message
		if event.eventtype() in ['privmsg', 'action']:
			
			if event.target() == self.nickname:
				# message is for the bot
				connection.privmsg(source_nickname, self.respond(event.arguments()[0]))
				return
			
			elif not irclib.is_channel(event.target()[0]):
				# search if the IRC user who sent the message is in one of the bridges
				for bridge in self.iter_bridges(irc_server=connection.server):
					try:
						from_ = bridge.get_participant(source_nickname)
						# he is, forward the message on XMPP
						if event.eventtype() == 'action':
							action = True
						else:
							action = False
						from_.say_on_xmpp_to(connection.nickname, event.arguments()[0], action=action)
						return
					except Bridge.NoSuchParticipantException:
						continue
				
				# he isn't, send an error
				connection.privmsg(source_nickname, 'XIB error: you cannot send a private message to an XMPP user if you are not in one of the chans he is in')
		
		
		# Connection errors
		if event.eventtype() in ['disconnect', 'kill', 'error']:
			if len(event.arguments()) > 0 and event.arguments()[0] == 'Connection reset by peer':
				self.error(2, debug_str, debug=True)
			else:
				self.error(say_levels.debug, debug_str, send_to_admins=True)
			return
		
		
		# Chan errors
		if event.eventtype() in ['cannotsendtochan', 'notonchannel']:
			self.error(2, debug_str, debug=True)
			
			bridge = self.get_bridge(irc_room=event.arguments()[0], irc_server=connection.server)
			
			if event.eventtype() == 'cannotsendtochan':
				if connection.nickname == self.nickname:
					bridge._join_irc_failed(event.eventtype())
				else:
					p = bridge.get_participant(connection.nickname)
					p._close_irc_connection(event.eventtype())
					p.irc_connection = event.eventtype()
			
			elif event.eventtype() == 'notonchannel':
				if connection.nickname == self.nickname:
					bridge.restart(message='Restarting bridge because we received the IRC event '+event.eventtype())
				else:
					p = bridge.get_participant(connection.nickname)
					p.irc_connection.join(bridge.irc_room)
			
			return
		
		
		# Ignore events not received on bot connection
		if connection.nickname != self.nickname:
			self.error(1, 'ignoring IRC '+event.eventtype()+' not received on bridge connection', debug=True)
			return
		
		
		# Server events
		if event.eventtype() in ['quit', 'nick']:
			for bridge in self.iter_bridges(irc_server=connection.server):
				
				try:
					from_ = bridge.get_participant(source_nickname)
				except Bridge.NoSuchParticipantException:
					continue
				
				handled = True
				
				# Quit event
				if event.eventtype() == 'quit':
					if len(event.arguments()) > 0:
						leave_message = event.arguments()[0]
					else:
						leave_message = 'Left server.'
					bridge.remove_participant('irc', from_.nickname, leave_message)
					continue
				
				# Nickname change
				if event.eventtype() == 'nick':
					from_.change_nickname(event.target(), 'xmpp')
					continue
			
			if handled:
				return
		
		
		# Chan events
		if event.eventtype() in ['pubmsg', 'action', 'part', 'kick', 'mode', 'join']:
			
			if event.eventtype() in ['pubmsg', 'action', 'part', 'kick', 'join'] and not source_nickname:
				self.error(say_levels.debug, 'a source is needed for a '+event.eventtype()+' event'+event_str)
				return
			
			if event.eventtype() in ['kick', 'mode'] and len(event.arguments()) == 0:
				self.error(say_levels.debug, 'at least 1 argument is needed for a '+event.eventtype()+' event'+event_str)
				return
			
			chan = event.target().lower()
			
			bridge = self.get_bridge(irc_room=chan, irc_server=connection.server)
			
			from_ = None
			if source_nickname:
				try:
					from_ = bridge.get_participant(source_nickname)
				except Bridge.NoSuchParticipantException:
					pass
			
			
			# Join event
			if event.eventtype() == 'join':
				bridge.add_participant('irc', source_nickname)
				return
			
			
			# kick handling
			if event.eventtype() == 'kick':
				try:
					kicked = bridge.get_participant(event.arguments()[0])
				except Bridge.NoSuchParticipantException:
					self.error(say_levels.debug, 'a participant that was not here has been kicked ? WTF ?'+event_str)
					return
				
				leave_message = 'kicked by '+source_nickname
				if len(event.arguments()) > 1:
					leave_message += ' with reason: '+event.arguments()[1]
				else:
					leave_message += ' (no reason was given)'
				log_message = '"'+kicked.nickname+'" has been '+leave_message
				
				self.error(say_levels.warning, log_message)
				
				if isinstance(kicked.irc_connection, irclib.ServerConnection):
					# an IRC duplicate of an XMPP user has been kicked, auto-rejoin
					kicked.irc_connection.join(bridge.irc_room)
				elif isinstance(kicked.xmpp_c, xmpp.client.Client):
					# an IRC user has been kicked, make its duplicate leave
					kicked.leave(leave_message)
				else:
					# an IRC user with no duplicate on XMPP has been kicked, say it on XMPP
					bridge.say(say_levels.warning, log_message, on_irc=False)
				return
			
			
			# Part event
			if event.eventtype() == 'part':
				if not from_:
					self.error(say_levels.debug, 'a participant that wasn\'t here left:'+event_str)
					return
				if len(event.arguments()) > 0:
					leave_message = event.arguments()[0]
				else:
					leave_message = 'Left channel.'
				bridge.remove_participant('irc', from_.nickname, leave_message)
				return
			
			
			# Chan message
			if event.eventtype() in ['pubmsg', 'action']:
				message = event.arguments()[0]
				if event.eventtype() == 'action':
					action = True
				else:
					action = False
				if isinstance(from_, Participant):
					from_.say_on_xmpp(message, action=action)
				else:
					bridge.say_on_behalf(source_nickname, message, 'xmpp', action=action)
				return
			
			
			# Mode event
			if event.eventtype() == 'mode':
				if len(event.arguments()) == 1:
					# chan mode
					self.error(1, 'ignoring IRC mode "'+event.arguments()[0]+'" for chan "'+event.target()+'"', debug=True)
				elif len(event.arguments()) == 2:
					# participant mode
					if event.arguments()[1] != self.nickname or not 'o' in event.arguments()[0]:
						self.error(1, 'ignoring IRC mode "'+event.arguments()[0]+'" for "'+event.arguments()[1]+'" in chan "'+event.target()+'"', debug=True)
						return
					if re.search('\+[^\-]*o', event.arguments()[0]):
						# bot is channel operator
						bridge.irc_op = True
						self.error(say_levels.notice, 'bot has IRC operator privileges in '+chan)
					elif re.search('\-[^\+]*o', event.arguments()[0]):
						# bot lost channel operator privileges
						if bridge.irc_op:
							self.error(say_levels.notice, 'bot lost IRC operator privileges in '+chan, send_to_admins=True)
						bridge.irc_op = False
				else:
					# unknown mode
					self.error(say_levels.debug, 'unknown IRC "mode" event (has 3 arguments):'+event_str)
				return
		
		
		# Namreply event
		if event.eventtype() == 'namreply':
			bridge = self.get_bridge(irc_room=event.arguments()[1].lower(), irc_server=connection.server)
			for nickname in re.split('(?:^[&@\+%]?|(?: [&@\+%]?)*)', event.arguments()[2].strip()):
				if nickname == '' or nickname == self.nickname:
					continue
				bridge.add_participant('irc', nickname)
			return
		
		
		# Unhandled events
		self.error(1, 'event not handled', debug=True)
		self._send_message_to_admins(say_levels.debug, 'The following IRC event was not handled:'+event_str)
	
	
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
	
	
	def format_message(self, importance, message):
		if importance < 0 or importance >= len(say_levels.levels):
			raise Exception('[Internal Error] unknown message importance')
		return'['+str(say_levels.get(importance))+'] '+message
	
	
	def get_bridge(self, **kwargs):
		"""Calls self.iter_bridges and raises exceptions when there are 0 or more than 1 matches
		
		See Bot.iter_bridges for the list of args"""
		
		bridges = [b for b in self.iter_bridges(**kwargs)]
		if len(bridges) == 0:
			raise KeyError, 'no bridge matching '+str(kwargs)
		elif len(bridges) > 1:
			raise Exception, 'more than one bridge matching '+str(kwargs)+'\n'+'\n'.join([str(b) for b in bridges])
		return bridges[0]
	
	
	def iter_bridges(self, irc_room=None, irc_server=None, xmpp_room_jid=None, patterns=None):
		for bridge in self.bridges:
			if irc_room != None and bridge.irc_room != irc_room:
				continue
			if irc_server != None and bridge.irc_server != irc_server:
				continue
			if xmpp_room_jid != None and bridge.xmpp_room_jid != xmpp_room_jid:
				continue
			if patterns != None:
				for pattern in patterns:
					if not pattern in str(bridge):
						continue
			yield bridge
	
	
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
	
	
	def remove_bridge(self, bridge, message='Removing bridge', log=True):
		self.bridges.remove(bridge)
		bridge.stop(message=message, log=log)
	
	
	def respond(self, message, participant=None, bot_admin=False):
		if isinstance(participant, Participant):
			bridge = participant.bridge
			if bot_admin != participant.bot_admin:
				bot_admin = participant.bot_admin
		else:
			bridge = None
		
		return commands.execute(self, message, bot_admin, bridge)
	
	
	def restart(self, message='Restarting bot'):
		# Stop the bridges
		for b in self.bridges:
			b.stop(message=message, log=False)
		
		# Reopen the bot's XMPP connection
		self.reopen_xmpp_connection(self.xmpp_c)
		
		# Restart the bridges
		for b in self.bridges:
			b.init2()
		
		self.error(-1, 'Bot restarted with message: '+message, send_to_admins=True)
	
	
	def restart_bridges_delayed(self, bridges, delay, error, protocol='xmpp'):
		if len(bridges) > 0:
			found = False
			error[1] += '\nThese bridges will be stopped:'
			for b in bridges:
				if protocol == 'xmpp':
					leave_message = 'Could not connect to the MUC server ('+b.xmpp_room_jid+')'
				else:
					leave_message = 'Could not connect to the IRC server ('+b.irc_connection._server_str()+')'
				
				if not b.reconnecting:
					found = True
					error[1] += '\n'+str(b)
					leave_message += 'will try to recreate the bridge in '+str(delay)+' seconds'
					b.reconnecting = True
					self.irc.execute_delayed(delay, b.init2)
				
				b.stop(message=leave_message, log=False)
		
		if found:
			self.error(error[0], error[1], send_to_admins=True)
	
	
	def stop(self, message='Stopping bot'):
		self.error(-1, message, send_to_admins=True)
		for bridge in self.bridges:
			bridge.stop(message=message, log=False)
	
	
	def __del__(self):
		for bridge in self.bridges:
			self.remove_bridge(bridge, message='Stopping bot', log=False)
		self.halt = True
