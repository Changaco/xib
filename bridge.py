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


import muc
xmpp = muc.xmpp
del muc
from participant import *
from encoding import *


class NoSuchParticipantException(Exception): pass


class bridge:
	def __init__(self, owner_bot, xmpp_room_jid, irc_room, irc_server, irc_port=6667, mode='normal'):
		self.bot = owner_bot
		self.irc_server = irc_server
		self.irc_port = irc_port
		self.irc_room = irc_room
		self.participants = []
		self.mode = mode
		
		# Join IRC room
		self.irc_connection = self.bot.irc.server()
		self.irc_connection.nick_callback = self._irc_nick_callback
		self.irc_connection.bridge = self
		try:
			self.irc_connection.connect(irc_server, irc_port, self.bot.nickname)
		except:
			self.bot.error('Error: joining IRC room failed')
			raise
		
		# Join XMPP room
		try:
			self.xmpp_room = xmpp.muc(xmpp_room_jid)
			self.xmpp_room.join(self.bot.xmpp_c, self.bot.nickname)
		except:
			self.bot.error('Error: joining XMPP room failed')
			raise
	
	
	def _irc_nick_callback(self, error):
		if error == None:
			self.irc_connection.join(self.irc_room)
			self.irc_connection.nick_callback = None
			self.bot.error('===> Debug: successfully connected on IRC side of bridge "'+str(self)+'"', debug=True)
		elif self.protocol != 'both':
			if error == 'nicknameinuse':
				self.bot.error('Error: "'+self.bot.nickname+'" is already used in the IRC chan of bridge "'+str(self)+'"')
				raise Exception('Error: "'+self.bot.nickname+'" is already used in the IRC chan of bridge "'+str(self)+'"')
			elif error == 'erroneusnickname':
				self.bot.error('Error: "'+self.bot.nickname+'" got "erroneusnickname" on bridge "'+str(self)+'"')
				raise Exception('Error: "'+self.bot.nickname+'" got "erroneusnickname" on bridge "'+str(self)+'"')
	
	
	def addParticipant(self, protocol, nickname):
		"""Add a participant to the bridge."""
		if (protocol == 'irc' and nickname == self.irc_connection.get_nickname()) or (protocol == 'xmpp' and nickname == self.xmpp_room.nickname):
			raise Exception('Internal Error: cannot add self')
		try:
			p = self.getParticipant(nickname)
			if p.protocol != protocol:
				if protocol == 'irc':
					p.createDuplicateOnXMPP()
				elif protocol == 'xmpp':
					p.createDuplicateOnIRC()
				else:
					raise Exception('Internal Error: bad protocol')
			return
		except NoSuchParticipantException:
			pass
		self.bot.error('===> Debug: adding participant "'+nickname+'" from "'+protocol+'" to bridge "'+str(self)+'"', debug=True)
		p = participant(self, protocol, nickname)
		self.participants.append(p)
		if self.mode != 'normal' and protocol == 'xmpp':
			xmpp_participants_nicknames = self.get_xmpp_participants_nicknames_list()
			self.say('[Info] Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
		return p
	
	
	def getParticipant(self, nickname):
		"""Returns a participant object if there is a participant using nickname in the bridge. Raises a NoSuchParticipantException otherwise."""
		for participant_ in self.participants:
			if participant_.nickname == nickname:
				return participant_
		raise NoSuchParticipantException('there is no participant using the nickname "'+nickname+'" in this bridge')
	
	
	def get_xmpp_participants_nicknames_list(self):
		xmpp_participants_nicknames = []
		for p in self.participants:
			if p.protocol == 'xmpp':
				xmpp_participants_nicknames.append(p.nickname)
		return xmpp_participants_nicknames
	
	
	def removeParticipant(self, protocol, nickname, leave_message):
		"""Remove the participant using nickname from the bridge. Raises a NoSuchParticipantException if nickname is not used in the bridge."""
		p = self.getParticipant(nickname)
		if p.protocol == 'both':
			self.bot.error('===> Debug: "'+nickname+'" was on both sides of bridge "'+str(self)+'" but left '+protocol, debug=True)
			if protocol == 'xmpp':
				p.protocol = 'irc'
				p.createDuplicateOnXMPP()
			elif protocol == 'irc':
				p.protocol = 'xmpp'
				p.createDuplicateOnIRC()
			else:
				raise Exception('Internal Error: bad protocol')
		else:
			self.bot.error('===> Debug: removing participant "'+nickname+'" from bridge "'+str(self)+'"', debug=True)
			self.participants.remove(p)
			p.leave(leave_message)
			i = 0
			for p in self.participants:
				if p.protocol == 'irc':
					i += 1
			if protocol == 'xmpp' and self.irc_connections_limit >= i:
				self.switchToNormalMode()
			del p
	
	
	def say(self, message, on_irc=True, on_xmpp=True):
		if on_xmpp == True:
			self.xmpp_room.say(message)
		if on_irc == True:
			self.irc_connection.privmsg(self.irc_room, auto_encode(message))
	
	
	def switchToNormalMode(self):
		if self.mode == 'normal':
			return
		prev_mode = self.mode
		self.mode = 'normal'
		for p in self.participants:
			if p.protocol == 'xmpp':
				p.createDuplicateOnIRC()
			elif p.protocol == 'irc' and prev_mode == 'minimal':
				p.createDuplicateOnXMPP()
		self.bot.error('===> Bridge is switching to normal mode.')
		self.say('[Notice] Bridge is switching to normal mode.')
	
	
	def switchToLimitedMode(self):
		if self.mode == 'limited':
			return
		self.mode = 'limited'
		i = 0
		for p in self.participants:
			if p.protocol == 'xmpp':
				i += 1
				if p.irc_connection:
					p.irc_connection.closing = True
					p.irc_connection.disconnect('Bridge is switching to limited mode')
					p.irc_connection = None
		self.irc_connections_limit = i
		self.bot.error('===> Bridge is switching to limited mode.')
		self.say('[Warning] Bridge is switching to limited mode, it means that it will be transparent for XMPP users but not for IRC users, this is due to the IRC servers\' per-IP-address connections\' limit number.')
		xmpp_participants_nicknames = self.get_xmpp_participants_nicknames_list()
		self.say('[Info] Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
	
	
	def __str__(self):
		return self.irc_room+'@'+self.irc_server+' <-> '+self.xmpp_room.room_jid
	
	
	def __del__(self):
		# Delete participants objects
		for p in self.participants:
			p.leave('Removing bridge')
			del p
		# Leave IRC room
		self.irc_connection.quit('Removing bridge')
		# Close IRC connection
		self.irc_connection.close()
		del self.irc_connection
		# Leave XMPP room
		self.xmpp_room.leave('Removing bridge')