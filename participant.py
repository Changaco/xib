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
from irclib import ServerNotConnectedError, ServerConnection
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
		if isinstance(self.xmpp_c, xmpp.client.Client) or isinstance(self.irc_connection, ServerConnection) or self.bridge.mode == 'minimal' or self.nickname == 'ChanServ':
			return
		self.xmpp_c = self.bridge.bot.get_xmpp_connection(self.nickname)
		self.muc = xmpp.muc(self.bridge.xmpp_room.room_jid)
		self.muc.join(self.xmpp_c, self.nickname, status='From IRC', callback=self._xmpp_join_callback)
	
	
	def _xmpp_join_callback(self, errors):
		if len(errors) == 0:
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on XMPP side of bridge "'+str(self.bridge)+'"', debug=True)
		elif self.xmpp_c != 'both':
			for error in errors:
				try:
					raise error
				except xmpp.muc.NicknameConflict:
					self.bridge.bot.error('===> Debug: "'+self.nickname+'" is already used in the XMPP MUC or reserved on the XMPP server of bridge "'+str(self.bridge)+'"', debug=True)
					self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used on both rooms or reserved on the XMPP server, please avoid that if possible')
					if self.muc.connected == True:
						self.muc.leave('Changed nickname to "'+self.nickname+'"')
				except xmpp.muc.RoomIsFull:
					self.bridge.bot.error('[Warning] XMPP MUC of bridge "'+str(self.bridge)+'" is full', send_to_admins=True)
					self.bridge.say('[Warning] XMPP room is full')
				
				if isinstance(self.xmpp_c, xmpp.client.Client):
					self.bridge.bot.close_xmpp_connection(self.nickname)
					self.xmpp_c = None
	
	
	def createDuplicateOnIRC(self):
		if isinstance(self.xmpp_c, xmpp.client.Client) or isinstance(self.irc_connection, ServerConnection) or self.bridge.mode != 'normal':
			return
		sleep(1) # try to prevent "reconnecting too fast" shit
		self.irc_connection = self.bridge.bot.irc.server(self.bridge.irc_server, self.bridge.irc_port, self.nickname)
		self.irc_connection.connect(nick_callback=self._irc_nick_callback)
	
	
	def _irc_nick_callback(self, error, arguments=[]):
		if error == None:
			self.irc_connection.join(self.bridge.irc_room)
			self.bridge.bot.error('===> Debug: "'+self.nickname+'" duplicate succesfully created on IRC side of bridge "'+str(self.bridge)+'"', debug=True)
		elif self.irc_connection != 'both':
			if error == 'nicknameinuse':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" is used or reserved on the IRC server of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used or reserved on the IRC server, please avoid that if possible')
			elif error == 'nickcollision':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" is used or reserved on the IRC server of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is used or reserved on the IRC server, please avoid that if possible')
			elif error == 'erroneusnickname':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" got "erroneusnickname" on bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" contains unauthorized characters and cannot be used in the IRC channel, please avoid that if possible')
			elif error == 'nicknametoolong':
				self.bridge.bot.error('===> Debug: "'+self.nickname+'" got "nicknametoolong" on bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] The nickname "'+self.nickname+'" is too long (limit seems to be '+str(arguments[0])+') and cannot be used in the IRC channel, please avoid that if possible')
			else:
				self.bridge.bot.error('===> Debug: unknown error while adding "'+self.nickname+'" to IRC side of bridge "'+str(self.bridge)+'"', debug=True)
				self.bridge.say('[Warning] unknown error while adding "'+self.nickname+'" to IRC side of bridge')
			
			if isinstance(self.irc_connection, ServerConnection):
				self.irc_connection.close('')
				self.irc_connection = error
	
	
	def changeNickname(self, newnick, on_protocol):
		"""Change participant's nickname."""
		
		oldnick = self.nickname
		
		if self.protocol == 'xmpp':
			if on_protocol == 'xmpp':
				self._close_irc_connection('unwanted nick change')
				self.irc_connection = 'unwanted nick change'
			
			else:
				self.nickname = newnick
				if isinstance(self.irc_connection, ServerConnection):
					if self.irc_connection.used_by == 1:
						self.irc_connection.nick(newnick, callback=self._irc_nick_callback)
					else:
						self._close_irc_connection(self, 'Changed nickname')
						self.createDuplicateOnIRC()
				else:
					if self.irc_connection == 'both':
						self.bridge.addParticipant('irc', oldnick)
					self.createDuplicateOnIRC()
		
		elif self.protocol == 'irc':
			if on_protocol == 'irc':
				self._close_xmpp_connection('unwanted nick change')
				self.xmpp_c = 'unwanted nick change'
			
			else:
				self.nickname = newnick
				if isinstance(self.xmpp_c, xmpp.client.Client):
					for b in self.bridge.bot.bridges:
						if b.hasParticipant(oldnick) and b.irc_server != self.bridge.irc_server:
							self.muc.leave(message='Changed nickname to "'+self.nickname+'"')
							self.xmpp_c = None
							self.bridge.bot.close_xmpp_connection(oldnick)
							self.createDuplicateOnXMPP()
							return
					
					if not self.bridge.bot.xmpp_connections.has_key(newnick):
						if self.bridge.bot.xmpp_connections.has_key(oldnick):
							self.bridge.bot.xmpp_connections.pop(oldnick)
						self.bridge.bot.xmpp_connections[newnick] = self.xmpp_c
					
					self.muc.change_nick(newnick, status='From IRC', callback=self._xmpp_join_callback)
				else:
					if self.xmpp_c == 'both':
						self.bridge.addParticipant('xmpp', oldnick)
					self.createDuplicateOnXMPP()
	
	
	def sayOnIRC(self, message):
		try:
			bot_say = False
			if message[:4] == '/me ':
				action = True
				message = message[4:]
			else:
				action = False
			if isinstance(self.irc_connection, ServerConnection):
				try:
					if action:
						self.irc_connection.action(self.bridge.irc_room, message)
					else:
						self.irc_connection.privmsg(self.bridge.irc_room, message)
				except ServerNotConnectedError:
					bot_say = True
			elif not isinstance(self.xmpp_c, xmpp.client.Client):
				bot_say = True
			if bot_say:
				if action:
					self.bridge.irc_connection.privmsg(self.bridge.irc_room, '* '+self.nickname+' '+message)
				else:
					self.bridge.irc_connection.privmsg(self.bridge.irc_room, '<'+self.nickname+'> '+message)
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnIRCTo(self, to, message):
		if isinstance(self.irc_connection, ServerConnection):
			try:
				self.irc_connection.privmsg(to, message)
			except EncodingException:
				self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
		elif not isinstance(self.xmpp_c, xmpp.client.Client):
			if self.bridge.mode != 'normal':
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
			else:
				self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an IRC duplicate with your nickname.')
	
	
	def sayOnXMPP(self, message):
		try:
			if isinstance(self.xmpp_c, xmpp.client.Client):
				self.muc.say(auto_decode(message))
			elif not isinstance(self.irc_connection, ServerConnection):
				if message[:4] == '/me ':
					self.bridge.xmpp_room.say('* '+self.nickname+' '+auto_decode(message[4:]))
				else:
					self.bridge.xmpp_room.say('<'+self.nickname+'> '+auto_decode(message))
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def sayOnXMPPTo(self, to, message):
		try:
			if isinstance(self.xmpp_c, xmpp.client.Client):
				self.muc.sayTo(to, auto_decode(message))
			elif not isinstance(self.irc_connection, ServerConnection):
				if self.bridge.mode != 'normal':
					self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
				else:
					self.bridge.getParticipant(to).sayOnXMPPTo(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an XMPP duplicate with your nickname.')
		except EncodingException:
			self.bridge.say('[Warning] "'+self.nickname+'" is sending messages using an unknown encoding')
	
	
	def leave(self, message):
		if message == None:
			message = ''
		self._close_xmpp_connection(message)
		self._close_irc_connection(message)
		self.nickname = None
	
	
	def _close_xmpp_connection(self, message):
		if isinstance(self.xmpp_c, xmpp.client.Client):
			self.muc.leave(auto_decode(message))
			self.bridge.bot.close_xmpp_connection(self.nickname)
	
	
	def _close_irc_connection(self, message):
		if isinstance(self.irc_connection, ServerConnection):
			if self.irc_connection.really_connected == True:
				self.irc_connection.part(self.bridge.irc_room, message=message)
			self.irc_connection.used_by -= 1
			if self.irc_connection.used_by < 1:
				self.irc_connection.close(message)
			self.irc_connection = None
	
	
	def __str__(self):
		r = 'self.protocol='+str(self.protocol)+'\n'+'self.nickname='+str(self.nickname)
		if isinstance(self.irc_connection, ServerConnection):
			r += '\nself.irc_connection='+str(self.irc_connection)+'\n'+'self.irc_connection.really_connected='+str(self.irc_connection.really_connected)
		if isinstance(self.xmpp_c, xmpp.client.Client):
			r += '\nself.muc.connected='+str(self.muc.connected)
		return r
	
	
	def __del__(self):
		if self.nickname != None:
			self.leave('')