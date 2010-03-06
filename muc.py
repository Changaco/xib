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


import xmppony as xmpp
from time import sleep


class muc:
	
	class PasswordNeeded(Exception): pass
	class MembersOnlyRoom(Exception): pass
	class BannedFromRoom(Exception): pass
	class NicknameConflict(Exception): pass
	class RoomIsFull(Exception): pass
	class RoomIsLocked(Exception): pass
	class ForgotNickname(Exception): pass
	class UnknownError(Exception): pass
	class RemoteServerNotFound(Exception): pass
	
	def __init__(self, room_jid):
		self.room_jid = room_jid
		self.connected = False
		self.participants = {}
	
	
	def _join(self, callback=None):
		self.callback = callback
		self.xmpp_c.RegisterHandler('presence', self._xmpp_presence_handler)
		s = xmpp.protocol.Presence(to=self.jid, status=self.status, payload=[xmpp.simplexml.Node(tag='x', attrs={'xmlns': 'http://jabber.org/protocol/muc'}, payload=[xmpp.simplexml.Node(tag='history', attrs={'maxchars': '0'})])])
		self._send(s)
	
	
	def _send(self, stanza):
		try:
			self.xmpp_c.send(stanza)
		except IOError, xmpp.Conflict:
			self.xmpp_c.reconnectAndReauth()
			for m in self.xmpp_c.mucs:
				m.rejoin()
			self.xmpp_c.send(stanza)
	
	
	def join(self, xmpp_c, nickname, status=None, callback=None):
		"""Join room on xmpp_c connection using nickname"""
		self.jid = self.room_jid+'/'+nickname
		self.nickname = nickname
		self.status = status
		self.xmpp_c = xmpp_c
		self.xmpp_c.mucs.append(self)
		self._join(callback=callback)
	
	
	def _xmpp_presence_handler(self, xmpp_c, presence):
		if presence.getFrom() == self.jid:
			errors = []
			if presence.getAttr('type') == 'error':
				for c in presence.getChildren():
					if c.getName() == 'error':
						for cc in c.getChildren():
							if cc.getNamespace() == 'urn:ietf:params:xml:ns:xmpp-stanzas' and cc.getName() != 'text':
								err = c.getAttr('type')+' '+cc.getName()
								if err == 'auth not-authorized':
									# password-protected room
									errors.append(self.__class__.PasswordNeeded(self.jid))
								elif err == 'auth registration-required':
									# members-only room
									errors.append(self.__class__.MembersOnlyRoom(self.jid))
								elif err == 'auth forbidden':
									# banned from room
									errors.append(self.__class__.BannedFromRoom(self.jid))
								elif err == 'cancel conflict':
									# nickname conflict
									errors.append(self.__class__.NicknameConflict(self.jid))
								elif err == 'wait service-unavailable':
									# room is full
									errors.append(self.__class__.RoomIsFull(self.jid))
								elif err == 'cancel item-not-found':
									# room is locked
									errors.append(self.__class__.RoomIsLocked(self.jid))
								elif err == 'modify jid-malformed':
									# forgot to give a nickname
									errors.append(self.__class__.ForgotNickname(self.jid))
								elif err == 'cancel remote-server-not-found':
									# MUC server is down or doesn't exist
									errors.append(self.__class__.RemoteServerNotFound(self.jid))
								else:
									errors.append(self.__class__.UnknownError(presence.__str__(fancy=1).encode('utf-8')))
						break
				if len(errors) == 0:
					errors.append(self.__class__.UnknownError(presence.__str__(fancy=1).encode('utf-8')))
			else:
				self.connected = True
				xmpp_c.UnregisterHandler('presence', self._xmpp_presence_handler)
			if self.callback != None:
				self.callback(errors)
	
	
	def say(self, message, action=False):
		"""Say message in the room"""
		self.xmpp_c.lock.acquire()
		if action:
			message = '/me '+message
		s = xmpp.protocol.Message(to=self.room_jid, typ='groupchat', body=message)
		self._send(s)
		self.xmpp_c.lock.release()
	
	
	def say_to(self, to, message):
		"""Send a private message"""
		self.xmpp_c.lock.acquire()
		s = xmpp.protocol.Message(to=self.room_jid+'/'+to, typ='chat', body=message)
		self._send(s)
		self.xmpp_c.lock.release()
	
	
	def change_nick(self, nickname, status=None, callback=None):
		"""Change nickname"""
		self.jid = self.room_jid+'/'+nickname
		self.callback = callback
		self.xmpp_c.RegisterHandler('presence', self._xmpp_presence_handler)
		self.xmpp_c.lock.acquire()
		s = xmpp.protocol.Presence(to=self.jid, status=status)
		self._send(s)
		self.xmpp_c.lock.release()
	
	
	def leave(self, message=''):
		"""Leave the room"""
		self.xmpp_c.lock.acquire()
		s = xmpp.protocol.Presence(to=self.jid, typ='unavailable', status=message)
		self._send(s)
		self.connected = False
		self.xmpp_c.lock.release()
	
	
	def rejoin(self, callback=None):
		"""Rejoin room"""
		self.connected = False
		self._join(callback=callback)
	
	
	def __del__(self):
		if self.connected:
			self.leave()
		if self in self.xmpp_c.mucs:
			self.xmpp_c.mucs.remove(self)

xmpp.muc = muc