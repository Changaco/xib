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
from time import sleep

from irclib import ServerNotConnectedError, ServerConnection
import muc
xmpp = muc.xmpp
del muc

import say_levels


class Participant:
	def __init__(self, owner_bridge, protocol, nickname, real_jid=None):
		self.bot_admin = False
		self.real_jid = real_jid
		self.bridge = owner_bridge
		self.protocol = protocol
		self.nickname = nickname
		self.duplicate_nickname = self.nickname
		self.irc_connection = None
		self.xmpp_c = None
		self.muc = None
		self.left = False
		if protocol == 'xmpp' and self.bridge.mode in ['normal', 'bypass']:
			self.create_duplicate_on_irc()
		elif protocol == 'irc' and self.bridge.mode != 'minimal':
			self.create_duplicate_on_xmpp()
	
	
	def _get_new_duplicate_nickname(self):
		new_duplicate_nickname = self.duplicate_nickname
		for i in xrange(5):
			new_duplicate_nickname = new_duplicate_nickname+'_'
			if not self.bridge.has_participant(new_duplicate_nickname):
				return new_duplicate_nickname
		return None
	
	
	def create_duplicate_on_xmpp(self):
		if isinstance(self.xmpp_c, xmpp.client.Client) or isinstance(self.irc_connection, ServerConnection):
			return
		self.xmpp_c = self.bridge.bot.get_xmpp_connection(self.duplicate_nickname)
		self.muc = xmpp.muc(self.bridge.xmpp_room_jid)
		self.join_muc()
	
	
	def join_muc(self):
		self.muc.join(self.xmpp_c, self.duplicate_nickname, status='From IRC', callback=self._xmpp_join_callback)
	
	
	def _xmpp_join_callback(self, errors):
		if len(errors) == 0:
			m = '"'+self.nickname+'" duplicate succesfully created on XMPP side of bridge "'+str(self.bridge)+'"'
			if self.nickname != self.duplicate_nickname:
				m += ' using nickname "'+self.duplicate_nickname+'"'
				self.bridge.say(say_levels.info, '"'+self.nickname+'" will appear as "'+self.duplicate_nickname+'" on XMPP because its real nickname is reserved or contains unauthorized characters')
			self.bridge.bot.error(3, m, debug=True)
		elif self.xmpp_c != 'both':
			for error in errors:
				try:
					raise error
				except xmpp.muc.NicknameConflict as e:
					if xmpp.protocol.JID(e.args[0]).getResource() != self.duplicate_nickname:
						return
					
					if self.bridge.mode == 'bypass':
						new_duplicate_nickname = self._get_new_duplicate_nickname()
						if new_duplicate_nickname != None:
							self.bridge.bot.error(3, '"'+self.duplicate_nickname+'" is already used in the XMPP MUC or reserved on the XMPP server of bridge "'+str(self.bridge)+'", trying "'+new_duplicate_nickname+'"', debug=True)
							if self.duplicate_nickname == self.nickname:
								self.bridge.say(say_levels.info, 'The nickname "'+self.duplicate_nickname+'" is used on both rooms or reserved on the XMPP server')
							self.duplicate_nickname = new_duplicate_nickname
							if isinstance(self.xmpp_c, xmpp.client.Client):
								self.bridge.bot.close_xmpp_connection(self.nickname)
								self.xmpp_c = None
							self.create_duplicate_on_xmpp()
							return
					
					else:
						self.bridge.say(say_levels.warning, 'The nickname "'+self.nickname+'" is used on both rooms or reserved on the XMPP server', log=True)
						if self.muc.connected == True:
							self.muc.leave('Changed nickname to "'+self.nickname+'"')
				except xmpp.muc.RoomIsFull:
					self.bridge.say(say_levels.warning, 'XMPP room is full', log=True)
				except xmpp.muc.RemoteServerNotFound:
					self.bridge._RemoteServerNotFound_handler()
				
				if isinstance(self.xmpp_c, xmpp.client.Client):
					self.bridge.bot.close_xmpp_connection(self.nickname)
					self.xmpp_c = None
	
	
	def create_duplicate_on_irc(self):
		if isinstance(self.xmpp_c, xmpp.client.Client) or isinstance(self.irc_connection, ServerConnection):
			return
		self.irc_connection = self.bridge.bot.irc.open_connection(self.bridge.irc_server, self.bridge.irc_port, self.duplicate_nickname)
		self.irc_connection.connect(nick_callback=self._irc_nick_callback)
	
	
	def _irc_nick_callback(self, error, arguments=[]):
		if error == None:
			self.irc_connection.join(self.bridge.irc_room)
			m = '"'+self.nickname+'" duplicate succesfully created on IRC side of bridge "'+str(self.bridge)+'"'
			if self.nickname != self.duplicate_nickname:
				m += ' using nickname "'+self.duplicate_nickname+'"'
				self.bridge.say(say_levels.info, '"'+self.nickname+'" will appear as "'+self.duplicate_nickname+'" on IRC because its real nickname is reserved or contains unauthorized characters')
			self.bridge.bot.error(3, m, debug=True)
		
		elif self.irc_connection != 'both':
			
			if error in ['nicknameinuse', 'nickcollision']:
				if arguments[0].arguments()[0] != self.duplicate_nickname:
					return
				
				if self.bridge.mode == 'bypass':
					new_duplicate_nickname = self._get_new_duplicate_nickname()
					if new_duplicate_nickname != None:
						self.bridge.bot.error(3, '"'+self.duplicate_nickname+'" is already used or reserved on the IRC server of bridge "'+str(self.bridge)+'", trying "'+new_duplicate_nickname+'"', debug=True)
						if self.duplicate_nickname == self.nickname:
							self.bridge.say(say_levels.info, 'The nickname "'+self.duplicate_nickname+'" is used or reserved on the IRC server')
						self.duplicate_nickname = new_duplicate_nickname
						if isinstance(self.irc_connection, ServerConnection):
							self.irc_connection.close('')
							self.irc_connection = error
						self.create_duplicate_on_irc()
						return
				
				else:
					self.bridge.say(say_levels.warning, 'The nickname "'+self.nickname+'" is used or reserved on the IRC server', log=True)
			
			elif error == 'erroneusnickname':
				if self.bridge.mode == 'bypass':
					self.duplicate_nickname = re.sub('[^a-zA-Z]', '', self.nickname)
					if isinstance(self.irc_connection, ServerConnection):
						self.irc_connection.close('')
						self.irc_connection = error
					self.create_duplicate_on_irc()
					return
				else:
					self.bridge.say(say_levels.warning, 'The nickname "'+self.nickname+'" contains unauthorized characters and cannot be used in the IRC channel', log=True)
			
			elif error == 'nicknametoolong':
				self.bridge.say(say_levels.warning, 'The nickname "'+self.nickname+'" is too long (limit seems to be '+str(arguments[0])+') and cannot be used in the IRC channel', log=True)
			
			else:
				self.bridge.say(say_levels.warning, 'unknown error while adding "'+self.nickname+'" to IRC side of bridge', log=True)
			
			if isinstance(self.irc_connection, ServerConnection):
				self.irc_connection.close('')
				self.irc_connection = error
	
	
	def set_both_sides(self):
		self.bridge.say(say_levels.warning, 'The nickname "'+self.nickname+'" is used on both sides of the bridge', log=True)
		if isinstance(self.irc_connection, ServerConnection):
			self.irc_connection.close('')
		if self.irc_connection != 'both':
			self.irc_connection = 'both'
		if isinstance(self.muc, xmpp.muc):
			self.muc.leave('')
			self.bridge.bot.close_xmpp_connection(self.nickname)
		if self.xmpp_c != 'both':
			self.xmpp_c = 'both'
	
	
	def change_nickname(self, newnick, on_protocol):
		"""Change participant's nickname."""
		
		p = None
		oldnick = self.nickname
		
		if self.protocol == 'xmpp':
			if on_protocol == 'xmpp':
				self._close_irc_connection('unwanted nick change')
				self.irc_connection = 'unwanted nick change'
			
			else:
				try:
					p = self.bridge.get_participant(newnick)
				except self.bridge.NoSuchParticipantException:
					self.nickname = newnick
					self.duplicate_nickname = newnick
					has_connection = self.bridge.bot.irc.has_connection(self.bridge.irc_server, self.bridge.irc_port, self.duplicate_nickname)
					if isinstance(self.irc_connection, ServerConnection):
						if not has_connection and self.irc_connection.used_by == 1:
							self.irc_connection.nick(newnick, callback=self._irc_nick_callback)
						else:
							self._close_irc_connection('Changed nickname')
							self.create_duplicate_on_irc()
					else:
						if self.irc_connection == 'both':
							self.bridge.add_participant('irc', oldnick)
						self.create_duplicate_on_irc()
					return
		
		elif self.protocol == 'irc':
			if on_protocol == 'irc':
				self._close_xmpp_connection('unwanted nick change')
				self.xmpp_c = 'unwanted nick change'
			
			else:
				try:
					p = self.bridge.get_participant(newnick)
				except self.bridge.NoSuchParticipantException:
					self.nickname = newnick
					self.duplicate_nickname = newnick
					if isinstance(self.xmpp_c, xmpp.client.Client):
						for b in self.bridge.bot.bridges:
							if b.has_participant(oldnick) and b.irc_server != self.bridge.irc_server:
								self.muc.leave(message='Changed nickname to "'+self.nickname+'"')
								self.xmpp_c = None
								self.bridge.bot.close_xmpp_connection(oldnick)
								self.create_duplicate_on_xmpp()
								return
						
						if not self.bridge.bot.xmpp_connections.has_key(newnick):
							if self.bridge.bot.xmpp_connections.has_key(oldnick):
								self.bridge.bot.xmpp_connections.pop(oldnick)
							self.bridge.bot.xmpp_connections[newnick] = self.xmpp_c
						
						self.muc.change_nick(newnick, status='From IRC', callback=self._xmpp_join_callback)
					else:
						if self.xmpp_c == 'both':
							self.bridge.add_participant('xmpp', oldnick)
						self.create_duplicate_on_xmpp()
					return
		
		self.nickname = newnick
		self.duplicate_nickname = newnick
		
		if not isinstance(p, Participant):
			return
		
		if p.nickname == newnick:
			if p.protocol == self.protocol:
				# should never happen
				raise Exception('WTF ?')
			else:
				self.set_both_sides()
		elif p.duplicate_nickname == newnick:
			if p.protocol != self.protocol:
				# should never happen
				raise Exception('WTF ?')
			else:
				if self.protocol == 'xmpp':
					self.irc_connection = p.irc_connection
					p.irc_connection = None
				else:
					self.xmpp_c = p.xmpp_c
					self.muc = p.muc
					p.xmpp_c = None
					p.muc = None
				p.duplicate_nickname = p._get_new_duplicate_nickname()
				p.create_duplicate_on_xmpp()
		else:
			# should never happen
			raise Exception('WTF ?')
	
	
	def say_on_irc(self, message):
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
				self.irc_connection.connect()
				bot_say = True
		elif not isinstance(self.xmpp_c, xmpp.client.Client):
			bot_say = True
		if bot_say:
			self.bridge.say_on_behalf(self.nickname, message, 'irc', action=action)
	
	
	def say_on_irc_to(self, to, message):
		if isinstance(self.irc_connection, ServerConnection):
			self.irc_connection.privmsg(to, message)
		elif not isinstance(self.xmpp_c, xmpp.client.Client):
			if self.bridge.mode != 'normal':
				self.bridge.get_participant(to).say_on_xmpp_to(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
			else:
				self.bridge.get_participant(to).say_on_xmpp_to(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an IRC duplicate with your nickname.')
	
	
	def say_on_xmpp(self, message, action=False):
		if isinstance(self.xmpp_c, xmpp.client.Client):
			self.muc.say(message, action=action)
		elif not isinstance(self.irc_connection, ServerConnection):
			self.bridge.say_on_behalf(self.nickname, message, 'xmpp', action=action)
	
	
	def say_on_xmpp_to(self, to, message):
		if isinstance(self.xmpp_c, xmpp.client.Client):
			self.muc.say_to(to, message)
		elif not isinstance(self.irc_connection, ServerConnection):
			if self.bridge.mode != 'normal':
				self.bridge.get_participant(to).say_on_xmpp_to(self.nickname, 'Sorry but cross-protocol private messages are disabled in '+self.bridge.mode+' mode.')
			else:
				self.bridge.get_participant(to).say_on_xmpp_to(self.nickname, 'Sorry but you cannot send cross-protocol private messages because I don\'t have an XMPP duplicate with your nickname.')
	
	
	def leave(self, message):
		if message == None:
			message = ''
		self.left = True
		self._close_xmpp_connection(message)
		self._close_irc_connection(message)
	
	
	def _close_xmpp_connection(self, message):
		if isinstance(self.xmpp_c, xmpp.client.Client):
			self.muc.leave(message)
			self.xmpp_c = None
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
		self.leave('')
