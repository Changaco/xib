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
from irclib import ServerNotConnectedError
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
		self.muc = None
		if protocol == 'xmpp':
			self.createDuplicateOnIRC()
		elif protocol == 'irc':
			self.createDuplicateOnXMPP()
		else:
			raise Exception('[Internal Error] bad protocol')
	
	
	def createDuplicateOnXMPP(self):
		if self.xmpp_c != None or self.irc_connection != None or self.bridge.mode == 'minimal' or self.nickname == 'ChanServ':
			return
		self.xmpp_c = self.bridge.bot.get_xmpp_connection(self.nickname)
		self.muc = xmpp.muc(self.bridge.xmpp_room.room_jid)
		self.muc.join(self.xmpp_c, self.nickname, status='From IRC', callback=self._xmpp_join_callback)
	
	
	def _xmpp_join_callback(self, errors):
		if len(errors) == 0:
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on XMPP side of bridge "'+str(self.bridge)+'"', debug=True)
		else:
			for error in errors:
				try:
					raise error
				except xmpp.muc.NicknameConflict:
					self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used in the XMPP MUC or reserved on the XMPP server of bridge "'+str(self.bridge)+'"', debug=True)
					self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used on both rooms or reserved on the XMPP server, please avoid that if possible')
					self.muc.leave('Nickname change')
					self.bridge.bot.close_xmpp_connection(self.nickname)
					self.xmpp_c = None
	
	
	def createDuplicateOnIRC(self):
		if self.irc_connection != None or self.xmpp_c != None or self.bridge.mode != 'normal':
			return
		sleep(1) # try to prevent "reconnecting too fast" shit
		self.irc_connection = self.bridge.bot.irc.server(self.bridge.irc_server, self.bridge.irc_port, self.nickname)
		self.irc_connection.connect(nick_callback=self._irc_nick_callback)
	
	
	def _irc_nick_callback(self, error, arguments=[]):
		if error == None:
			self.irc_connection.join(self.bridge.irc_room)
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on IRC side of bridge "'+str(self.bridge)+'"', debug=True)
		else:
			if error == 'nicknameinuse':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used in the IRC chan of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used on both rooms or reserved on the IRC server, please avoid that if possible')
				if self.irc_connection != None:
					self.irc_connection.close('')
					self.irc_connection = None
			elif error == 'nickcollision':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used or reserved on the IRC server of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is already used or reserved on the IRC server, please avoid that if possible')
				if self.irc_connection != None:
					self.irc_connection.close('')
					self.irc_connection = None
			elif error == 'erroneusnickname':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" got "erroneusnickname" on bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" contains unauthorized characters and cannot be used in the IRC channel, please avoid that if possible')
				if self.irc_connection != None:
					self.irc_connection.close('')
					self.irc_connection = None
			elif error == 'nicknametoolong':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" got "nicknametoolong" on bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is too long (limit seems to be '+str(arguments[0])+') and cannot be used in the IRC channel, please avoid that if possible')
				if self.irc_connection != None:
					self.irc_connection.close('')
					self.irc_connection = None
	
	
	def changeNickname(self, newnick, on_protocol):
		"""Change participant's nickname."""
		
		oldnick = self.nickname
		
		if self.protocol == 'xmpp':
			if on_protocol == 'xmpp':
				self.bridge.removeParticipant('irc', self.nickname, '')
				self.bridge.addParticipant('irc', newnick)
			
			else:
				self.nickname = newnick
				if self.irc_connection != None:
					self.irc_connection.nick(newnick, callback=self._irc_nick_callback)
				else:
					self.createDuplicateOnIRC()
		
		elif self.protocol == 'irc':
			if on_protocol == 'irc':
				self.bridge.removeParticipant('xmpp', self.nickname, '')
				self.bridge.addParticipant('xmpp', newnick)
			
			else:
				self.nickname = newnick
				if self.muc != None:
					for b in self.bridge.bot.bridges:
						if b.hasParticipant(oldnick) and b.irc_server != self.bridge.irc_server:
							self.muc.leave(message='Nickname change')
							self.xmpp_c = None
							self.bridge.bot.close_xmpp_connection(oldnick)
							self.createDuplicateOnXMPP()
							return
					
					if not self.bridge.bot.xmpp_connections.has_key(newnick):
						self.bridge.bot.xmpp_connections.pop(oldnick)
						self.bridge.bot.xmpp_connections[newnick] = self.xmpp_c
					
					self.muc.change_nick(newnick, status='From IRC', callback=self._xmpp_join_callback)
				else:
					self.createDuplicateOnXMPP()
	
	
	def sayOnIRC(self, message):
		try:
			if self.irc_connection != None:
				try:
					self.irc_connection.privmsg(self.bridge.irc_room, message)
				except ServerNotConnectedError:
					self.bridge.irc_connection.privmsg(self.bridge.irc_room, '<'+self.nickname+'> '+message)
			elif self.xmpp_c == None:
				self.bridge.irc_connection.privmsg(self.bridge.irc_room, '<'+self.nickname+'> '+message)
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnIRCTo(self, to, message):
		if self.irc_connection != None:
			try:
				self.irc_connection.privmsg(to, message)
			except EncodingException:
				self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
		elif self.xmpp_c == None:
			if self.bridge.mode != 'normal':
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
			else:
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an IRC duplicate with your nickname.')
	
	
	def sayOnXMPP(self, message):
		try:
			if self.xmpp_c != None:
				self.muc.say(auto_decode(message))
			elif self.irc_connection == None:
				self.bridge.xmpp_room.say('<'+self.nickname+'> '+auto_decode(message))
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnXMPPTo(self, to, message):
		try:
			if self.xmpp_c != None:
				self.muc.sayTo(to, auto_decode(message))
			elif self.irc_connection == None:
				if self.bridge.mode != 'normal':
					self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
				else:
					self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an XMPP duplicate with your nickname.')
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def leave(self, message):
		if message == None:
			message = ''
		if self.xmpp_c != None:
			self.muc.leave(auto_decode(message))
			self.bridge.bot.close_xmpp_connection(self.nickname)
		if self.irc_connection != None:
			self.irc_connection.used_by -= 1
			if self.irc_connection.used_by < 1:
				self.irc_connection.close(message)
			self.irc_connection = None
		self.nickname = None
	
	
	def __del__(self):
		if self.nickname != None:
			self.leave('')