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


# *** CONTRIBUTORS ***
# Contributor: Changaco <changaco@changaco.net>


# *** Changelog ***
# 0.1: First release


# *** Versioning ***
# Major will pass to 1 when xib will be considered fault-tolerant
# After that major will only be changed if the new version is not retro-compatible (e.g. requires changes in config file)

version = 0, 1


import irclib
import xmppony as xmpp
from threading import Thread
from bridge import *
from time import sleep
import re
import sys


class bot(Thread):
	
	def __init__(self, jid, password, nickname, error_fd=sys.stderr, debug=False):
		Thread.__init__(self)
		self.jid = xmpp.protocol.JID(jid=jid)
		self.nickname = nickname
		self.password = password
		self.error_fd = error_fd
		self.debug = debug
		self.bridges = []
		self.irc = irclib.IRC()
		self.irc.add_global_handler('all_events', self._irc_event_handler)
		self.irc_thread = Thread(target=self.irc.process_forever)
		self.irc_thread.start()
		# Open connection with XMPP server
		try:
			self.xmpp_c = xmpp.client.Client(self.jid.getDomain(), debug=[])
			self.xmpp_c.connect()
			if self.jid.getResource() == '':
				self.jid.setResource('xib-bot')
			self.xmpp_c.auth(self.jid.getNode(), password, resource=self.jid.getResource())
			self.xmpp_c.RegisterHandler('presence', self._xmpp_presence_handler)
			self.xmpp_c.RegisterHandler('iq', self._xmpp_iq_handler)
			self.xmpp_c.RegisterHandler('message', self._xmpp_message_handler)
			self.xmpp_c.sendInitPresence()
		except:
			self.error('Error: XMPP Connection failed')
			raise
		self.xmpp_thread = Thread(target=self._xmpp_loop)
		self.xmpp_thread.start()
	
	
	def error(self, s, debug=False):
		if not debug or debug and self.debug:
			try:
				self.error_fd.write(auto_encode(s)+"\n")
			except EncodingException:
				self.error_fd.write('Error message cannot be transcoded.\n')
	
	
	def _xmpp_loop(self):
		while True:
			self.xmpp_c.Process(5)
	
	
	def _xmpp_presence_handler(self, xmpp_c, presence):
		"""[Internal] Manage XMPP presence."""
		self.error('==> Debug: Received XMPP presence.', debug=True)
		self.error(presence.__str__(fancy=1), debug=True)
		
		if presence.getTo() != self.jid:
			#self.error('=> Debug: Skipping XMPP presence not received on bot connection.', debug=True)
			return
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
						if p.protocol in ['xmpp', 'both']:
							if presence.getType() == 'unavailable':
								x = presence.getTag('x', namespace='http://jabber.org/protocol/muc#user')
								if x and x.getTag('status', attrs={'code': '303'}):
									# participant changed its nickname
									item = x.getTag('item')
									if not item:
										self.error('Debug: bad stanza, no item element', debug=True)
										return
									new_nick = item.getAttr('nick')
									if not new_nick:
										self.error('Debug: bad stanza, new nick is not given', debug=True)
										return
									p.changeNickname(new_nick, 'irc')
									return
								# participant left
								bridge.removeParticipant('xmpp', resource, presence.getStatus())
					except NoSuchParticipantException:
						if presence.getType() != 'unavailable':
							try:
								bridge.addParticipant('xmpp', resource)
							except Exception:
								pass
				return
	
	
	def _xmpp_iq_handler(self, xmpp_c, iq):
		"""[Internal] Manage XMPP IQs."""
		self.error('=> Debug: Received XMPP iq.', debug=True)
		self.error(iq.__str__(fancy=1), debug=True)
	
	
	def _xmpp_message_handler(self, xmpp_c, message):
		"""[Internal] Manage XMPP messages."""
		if message.getType() == 'chat':
			self.error('==> Debug: Received XMPP message.', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
			if message.getTo() == self.jid:
				xmpp_c.send(xmpp.protocol.Message(to=message.getFrom(), body=u'Sorry I am a bot I don\'t speak …', typ='chat'))
			else:
				from_bare_jid = unicode(message.getFrom().getNode()+'@'+message.getFrom().getDomain())
				for bridge in self.bridges:
					if from_bare_jid == bridge.xmpp_room.room_jid:
						# message comes from a room participant
						try:
							to_ = bridge.getParticipant(message.getTo().getResource())
							from_ = bridge.getParticipant(message.getFrom().getResource())
						except NoSuchParticipantException:
							self.error('==> Debug: XMPP chat message not relayed, from_bare_jid='+from_bare_jid+'  to='+str(message.getTo().getResource())+'  from='+message.getFrom().getResource(), debug=True)
							return
						if from_.protocol in ['xmpp', 'both']:
							from_.sayOnIRCTo(to_.nickname, message.getBody())
						else:
							self.error('==> Debug: received XMPP chat message from a non-XMPP participant, WTF ?', debug=True)
		
		elif message.getType() == 'groupchat':
			# message comes from a room
			if message.getTo() != self.jid:
				self.error('=> Debug: Skipping XMPP MUC message not received on bot connection.', debug=True)
				return
			for child in message.getChildren():
				if child.getName() == 'delay':
					self.error('=> Debug: Skipping XMPP MUC delayed message.', debug=True)
					return
			self.error('==> Debug: Received XMPP message.', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
			from_ = xmpp.protocol.JID(message.getFrom())
			room_jid = unicode(from_.getNode()+'@'+from_.getDomain())
			for bridge in self.bridges:
				if room_jid == bridge.xmpp_room.room_jid:
					resource = unicode(from_.getResource())
					if resource == '':
						# message comes from the room itself
						pass
					else:
						# message comes from a participant of the room
						try:
							participant_ = bridge.getParticipant(resource)
						except NoSuchParticipantException:
							return
						if participant_.protocol == 'xmpp':
							participant_.sayOnIRC(message.getBody())
						elif participant_.protocol == 'both':
							bridge.irc_connection.privmsg(bridge.irc_room, '<'+participant_.nickname+'> '+message.getBody())
		else:
			self.error('==> Debug: Received XMPP message.', debug=True)
			self.error(message.__str__(fancy=1), debug=True)
	
	
	def _irc_event_handler(self, connection, event):
		"""[internal] Manage IRC events"""
		if not connection.bridge in self.bridges:
			# Not for us, ignore
			return
		if 'all' in event.eventtype():
			return
		if 'motd' in event.eventtype():
			self.error('=> Debug: ignoring event containing "motd" in the eventtype ('+event.eventtype()+')', debug=True)
			return
		if event.eventtype() in ['pong', 'welcome', 'yourhost', 'created', 'myinfo', 'featurelist', 'luserclient', 'luserop', 'luserchannels', 'luserme', 'n_local', 'n_global', 'endofnames', 'luserunknown']:
			self.error('=> Debug: ignoring '+event.eventtype(), debug=True)
			return
		if event.eventtype() == 'pubmsg' and connection.get_nickname() != connection.bridge.irc_connection.get_nickname():
			self.error('=> Debug: ignoring IRC pubmsg not received on bridge connection', debug=True)
			return
		if event.eventtype() == 'ping':
			connection.pong(connection.get_server_name())
			return
		self.error('==> Debug: Received IRC event.', debug=True)
		self.error('server='+connection.get_server_name(), debug=True)
		self.error('eventtype='+event.eventtype(), debug=True)
		self.error('source='+str(event.source()), debug=True)
		self.error('target='+str(event.target()), debug=True)
		self.error('arguments='+str(event.arguments()), debug=True)
		if event.eventtype() == 'disconnect':
			if connection.get_nickname() == connection.bridge.irc_connection.get_nickname():
				# Lost bridge IRC connection, we must reconnect if we want the bridge to work
				self.recreate_bridge(connection.bridge)
				return
			if connection.bridge.mode == 'normal' and connection.closing == False:
				connection.bridge.switchToLimitedMode()
			if connection.closing == True:
				connection.close()
			return
		elif event.eventtype() == 'nicknameinuse':
			if connection.nick_callback:
				connection.nick_callback('nicknameinuse')
			else:
				self.error('=> Debug: no nick callback for "'+str(event.target())+'"', debug=True)
			return
		elif event.eventtype() == 'erroneusnickname':
			if connection.nick_callback:
				connection.nick_callback('erroneusnickname')
			else:
				self.error('=> Debug: no nick callback for "'+str(event.target())+'"', debug=True)
			return
		elif event.eventtype() == 'umode':
			if connection.nick_callback:
				connection.nick_callback(None)
			else:
				self.error('=> Debug: no nick callback for "'+str(event.target())+'"', debug=True)
				self.error('connection.nick_callback='+str(connection.nick_callback), debug=True)
			return
		elif event.eventtype() == 'namreply':
			for nickname in re.split('(?:^[@\+]?|(?: [@\+]?)*)', event.arguments()[2].strip()):
				if nickname == '':
					continue
				try:
					connection.bridge.addParticipant('irc', nickname)
				except:
					pass
			return
		elif event.eventtype() == 'join':
			nickname = event.source().split('!')[0]
			if nickname == self.nickname:
				pass
			else:
				try:
					connection.bridge.getParticipant(nickname)
				except NoSuchParticipantException:
					connection.bridge.addParticipant('irc', nickname)
			return
		try:
			if not '!' in event.source():
				return
			from_ = connection.bridge.getParticipant(event.source().split('!')[0])
			if event.eventtype() == 'quit' or event.eventtype() == 'part' and event.target() == connection.bridge.irc_room:
				if from_.protocol in ['irc', 'both']:
					connection.bridge.removeParticipant('irc', from_.nickname, event.arguments()[0])
				return
			if event.eventtype() == 'nick' and from_.protocol in ['irc', 'both']:
				from_.changeNickname(event.target(), 'xmpp')
		except NoSuchParticipantException:
			self.error('===> Debug: NoSuchParticipantException "'+event.source().split('!')[0]+'"', debug=True)
			return
		if event.eventtype() == 'pubmsg':
			if from_.protocol == 'irc' or from_.protocol == 'both':
				from_.sayOnXMPP(event.arguments()[0])
		elif event.eventtype() == 'privmsg':
			if event.target() == None:
				return
			try:
				to_ = connection.bridge.getParticipant(event.target().split('!')[0])
			except NoSuchParticipantException:
				if event.target().split('!')[0] == self.nickname:
					connection.privmsg(from_.nickname, u'Sorry I am a bot I don\'t speak …')
				return
			if to_.protocol == 'xmpp':
				from_.sayOnXMPPTo(to_.nickname, event.arguments()[0])
	
	
	def new_bridge(self, xmpp_room, irc_room, irc_server, irc_port=6667):
		"""Create a bridge between xmpp_room and irc_room at irc_server."""
		b = bridge(self, xmpp_room, irc_room, irc_server, irc_port=irc_port)
		self.bridges.append(b)
		return b
	
	
	def recreate_bridge(self, bridge):
		"""Disconnect and reconnect."""
		self.new_bridge(bridge.xmpp_room.room_jid, bridge.irc_room, bridge.irc_server)
		self.bridges.remove(bridge)
		del bridge
	
	
	def __del__(self):
		for bridge in bridges:
			del bridge