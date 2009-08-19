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
	
	def __init__(self, room_jid):
		self.room_jid = room_jid
		self.connected = False
		self.participants = {}
	
	
	def join(self, xmpp_c, nickname, status=None, callback=None):
		"""Join room on xmpp_c connection using nickname"""
		self.jid = self.room_jid+'/'+nickname
		self.nickname = nickname
		self.xmpp_c = xmpp_c
		self.callback = callback
		self.xmpp_c.RegisterHandler('presence', self._xmpp_presence_handler)
		self.xmpp_c.send(xmpp.protocol.Presence(to=self.jid, status=status, payload=[xmpp.simplexml.Node(tag='x', attrs={'xmlns': 'http://jabber.org/protocol/muc'}, payload=[xmpp.simplexml.Node(tag='history', attrs={'maxchars': '0'})])]))
	
	
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
									errors.append(self.__class__.PasswordNeeded())
								elif err == 'auth registration-required':
									# members-only room
									errors.append(self.__class__.MembersOnlyRoom())
								elif err == 'auth forbidden':
									# banned from room
									errors.append(self.__class__.BannedFromRoom())
								elif err == 'cancel conflict':
									# nickname conflict
									errors.append(self.__class__.NicknameConflict())
								elif err == 'wait service-unavailable':
									# room is full
									errors.append(self.__class__.RoomIsFull())
								elif err == 'cancel item-not-found':
									# room is locked
									errors.append(self.__class__.RoomIsLocked())
								elif err == 'modify jid-malformed':
									# forgot to give a nickname
									errors.append(self.__class__.ForgotNickname())
								else:
									errors.append(self.__class__.UnknownError(presence.__str__(fancy=1).decode('utf-8')))
						break
				if len(errors) == 0:
					errors.append(self.__class__.UnknownError(presence.__str__(fancy=1).decode('utf-8')))
			else:
				self.connected = True
				xmpp_c.UnregisterHandler('presence', self._xmpp_presence_handler)
			if self.callback != None:
				self.callback(errors)
	
	
	def say(self, message):
		"""Say message in the room"""
		self.xmpp_c.send(xmpp.protocol.Message(to=self.room_jid, typ='groupchat', body=message))
	
	
	def sayTo(self, to, message):
		"""Send a private message"""
		self.xmpp_c.send(xmpp.protocol.Message(to=self.room_jid+'/'+to, typ='chat', body=message))
	
	
	def change_nick(self, nickname, status=None, callback=None):
		"""Change nickname"""
		self.jid = self.room_jid+'/'+nickname
		self.callback = callback
		self.xmpp_c.RegisterHandler('presence', self._xmpp_presence_handler)
		self.xmpp_c.send(xmpp.protocol.Presence(to=self.jid, status=status))
	
	
	def leave(self, message=''):
		"""Leave the room"""
		self.xmpp_c.send(xmpp.protocol.Presence(to=self.jid, typ='unavailable', status=message))
		self.connected = False
	
	
	def __del__(self):
		if self.connected:
			self.leave()

xmpp.muc = muc