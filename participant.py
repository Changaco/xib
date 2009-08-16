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
import irclib
from encoding import *
from threading import Thread
from time import sleep


class participant:
	def __init__(self, owner_bridge, protocol, nickname):
		self.bridge = owner_bridge
		self.protocol = protocol
		self.nickname = nickname
		self.irc_connection = None
		self.xmpp_c = None
		if protocol == 'xmpp':
			self.createDuplicateOnIRC()
		elif protocol == 'irc':
			self.createDuplicateOnXMPP()
		else:
			raise Exception('Internal Error: bad protocol')
			quit(1)
	
	
	def createDuplicateOnXMPP(self):
		if self.xmpp_c != None or self.irc_connection != None or self.protocol == 'both' or self.bridge.mode == 'minimal':
			return
		self.xmpp_c = xmpp.client.Client(self.bridge.bot.jid.getDomain(), debug=[])
		self.xmpp_c.connect()
		self.xmpp_c.auth(self.bridge.bot.jid.getNode(), self.bridge.bot.password, resource=self.nickname)
		self.xmpp_c.RegisterHandler('presence', self.bridge.bot._xmpp_presence_handler)
		self.xmpp_c.RegisterHandler('iq', self.bridge.bot._xmpp_iq_handler)
		self.xmpp_c.RegisterHandler('message', self.bridge.bot._xmpp_message_handler)
		self.xmpp_thread = Thread(target=self._xmpp_loop)
		self.xmpp_thread.start()
		self.xmpp_c.sendInitPresence()
		self.muc = xmpp.muc(self.bridge.xmpp_room.room_jid)
		self.muc.join(self.xmpp_c, self.nickname, status='From IRC', callback=self._xmpp_join_callback)
	
	
	def createDuplicateOnIRC(self):
		if self.irc_connection != None or self.xmpp_c != None or self.protocol == 'both' or self.bridge.mode != 'normal':
			return
		sleep(1) # try to prevent "reconnecting too fast" shit
		self.irc_connection = self.bridge.bot.irc.server()
		self.irc_connection.bridge = self.bridge
		self.irc_connection.nick_callback = self._irc_nick_callback
		self.irc_connection.connect(self.bridge.irc_server, self.bridge.irc_port, self.nickname)
	
	
	def _irc_nick_callback(self, error):
		if error == None:
			self.irc_connection.join(self.bridge.irc_room)
			self.irc_connection.nick_callback = None
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on IRC side of bridge "'+str(self.bridge)+'"', debug=True)
		elif self.protocol != 'both':
			if error == 'nicknameinuse':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used in the IRC chan of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used on both rooms or reserved on the IRC server, please avoid that if possible')
				self.protocol = 'both'
				self.irc_connection.close()
				self.irc_connection = None
			elif error == 'erroneusnickname':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" got "erroneusnickname" on bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" contains non-ASCII characters and cannot be used in the IRC channel, please avoid that if possible')
				self.irc_connection.close()
				self.irc_connection = None
	
	
	def _xmpp_join_callback(self, errors):
		if len(errors) == 0:
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on XMPP side of bridge "'+str(self.bridge)+'"', debug=True)
		elif self.protocol != 'both':
			for error in errors:
				try:
					raise error
				except xmpp.muc.NicknameConflict:
					self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used in the XMPP MUC or reserved on the XMPP server of bridge "'+str(self.bridge)+'"', debug=True)
					self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used on both rooms or reserved on the XMPP server, please avoid that if possible')
					self.protocol = 'both'
					self.xmpp_c = None
	
	
	def _xmpp_loop(self):
		while True:
			if self.xmpp_c != None:
				self.xmpp_c.Process(5)
			else:
				sleep(5)
	
	
	def changeNickname(self, newnick, on_protocol):
		if self.protocol == 'xmpp':
			if on_protocol == 'xmpp':
				raise Exception('Internal Error: wanted to change nickname on bad protocol')
			if self.irc_connection:
				self.irc_connection.nick(newnick)
			self.nickname = newnick
		elif self.protocol == 'irc':
			if on_protocol == 'irc':
				raise Exception('Internal Error: wanted to change nickname on bad protocol')
			if self.muc:
				self.muc.change_nick(newnick, callback=self._xmpp_join_callback)
			self.nickname = newnick
		elif self.protocol == 'both':
			if on_protocol == 'irc':
				self.protocol = 'xmpp'
				self.createDuplicateOnIRC()
			elif on_protocol == 'xmpp':
				self.protocol = 'irc'
				self.createDuplicateOnXMPP()
	
	
	def sayOnIRC(self, message):
		try:
			if self.protocol == 'irc':
				raise Exception('Internal Error: "'+self.nickname+'" comes from IRC')
			elif self.protocol == 'both' or self.irc_connection == None:
				self.bridge.irc_connection.privmsg(self.bridge.irc_room, '<'+self.nickname+'> '+message)
			else:
				self.irc_connection.privmsg(self.bridge.irc_room, message)
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnIRCTo(self, to, message):
		if self.protocol == 'irc':
			raise Exception('Internal Error: "'+self.nickname+'" comes from IRC')
		elif self.irc_connection == None:
			if self.bridge.mode != 'normal':
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but cross-protocol private messages are disabled in limited mode.')
			else:
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an IRC duplicate with your nickname.')
		else:
			try:
				self.irc_connection.privmsg(to, message)
			except EncodingException:
				self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnXMPP(self, message):
		if self.protocol == 'xmpp':
			raise Exception('Internal Error: "'+self.nickname+'" comes from XMPP')
		elif self.protocol == 'both' or self.xmpp_c == None:
			self.bridge.xmpp_room.say('<'+self.nickname+'> '+auto_decode(message))
		else:
			try:
				self.muc.say(auto_decode(message))
			except EncodingException:
				self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnXMPPTo(self, to, message):
		if self.protocol == 'xmpp':
			raise Exception('Internal Error: "'+self.nickname+'" comes from XMPP')
		else:
			try:
				self.muc.sayTo(to, auto_decode(message))
			except EncodingException:
				self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def leave(self, message):
		if message == None:
			message = ''
		try:
			self.muc.leave(message)
		except AttributeError:
			pass
		try:
			self.irc_connection.disconnect(message)
		except AttributeError:
			pass
		self.nickname = None
	
	
	def __del__(self):
		if self.nickname != None:
			self.leave('')