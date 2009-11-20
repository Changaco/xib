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
from irclib import ServerConnection
import traceback
import re
import threading


class NoSuchParticipantException(Exception): pass


class bridge:
	
	_all = 0
	_info = 1
	_notice = 2
	_warning = 3
	_error = 4
	_nothing = 5
	_modes = ['normal', 'limited', 'minimal']
	
	
	def __init__(self, owner_bot, xmpp_room_jid, irc_room, irc_server, mode, say_level, irc_port=6667):
		"""Create a new bridge."""
		self.bot = owner_bot
		self.irc_server = irc_server
		self.irc_port = irc_port
		self.irc_room = irc_room.lower()
		if hasattr(self.__class__, '_'+say_level):
			self.say_level = getattr(self.__class__, '_'+say_level)
		else:
			raise Exception('[Error] "'+say_level+'" is not a correct value for a bridge\'s "say_level" attribute')
		self.participants = []
		if mode not in self.__class__._modes:
			raise Exception('[Error] "'+mode+'" is not a correct value for a bridge\'s "mode" attribute')
		self.mode = mode
		
		self.lock = threading.RLock()
		
		# Join XMPP room
		try:
			self.xmpp_room = xmpp.muc(xmpp_room_jid)
			self.xmpp_room.join(self.bot.xmpp_c, self.bot.nickname, callback=self._xmpp_join_callback)
		except:
			self.bot.error('[Error] joining XMPP room failed')
			raise
		
		# Join IRC room
		try:
			self.irc_connections_limit = -1
			self.irc_connection = self.bot.irc.server(irc_server, irc_port, self.bot.nickname)
			self.irc_connection.connect(nick_callback=self._irc_nick_callback)
		except:
			self.bot.error('[Error] joining IRC room failed')
			raise
		
		self.bot.error('[Notice] bridge "'+str(self)+'" is running in '+self.mode+' mode and a say_level of "'+say_level+'"')
	
	
	def _irc_nick_callback(self, error, arguments=[]):
		if error == None:
			if self.mode == None:
				return
			self.irc_connection.join(self.irc_room)
			self.bot.error('===> Debug: successfully connected on IRC side of bridge "'+str(self)+'"', debug=True)
			self.say('[Notice] bridge "'+str(self)+'" is running in '+self.mode+' mode', on_xmpp=False)
		else:
			self.mode = None
			if self.xmpp_room.connected == True:
				self.say('[Error] failed to connect to the IRC chan, leaving ...', on_irc=False)
			try:
				if error == 'nicknameinuse':
					raise Exception('[Error] "'+self.bot.nickname+'" is already used in the IRC chan or reserved on the IRC server of bridge "'+str(self)+'"')
				elif error == 'nickcollision':
					raise Exception('[Error] "'+self.bot.nickname+'" is already used or reserved on the IRC server of bridge "'+str(self)+'"')
				elif error == 'erroneusnickname':
					raise Exception('[Error] "'+self.bot.nickname+'" got "erroneusnickname" on bridge "'+str(self)+'"')
				elif error == 'nicknametoolong':
					raise Exception('[Error] "'+self.bot.nickname+'" got "nicknametoolong" on bridge "'+str(self)+'", limit seems to be '+str(arguments[0]))
				else:
					raise Exception('[Error] unknown error for "'+self.bot.nickname+'" on bridge "'+str(self)+'", limit seems to be '+str(arguments[0]))
			except:
				traceback.print_exc()
			self.bot.error('[Error] failed to connect to the IRC chan of bridge "'+str(self)+'", removing bridge', send_to_admins=True)
			self.bot.removeBridge(self)
	
	
	def _xmpp_join_callback(self, errors):
		"""Called by muc._xmpp_presence_handler"""
		if len(errors) == 0:
			if self.mode == None:
				return
			self.bot.error('===> Debug: succesfully connected on XMPP side of bridge "'+str(self)+'"', debug=True)
			self.say('[Notice] bridge "'+str(self)+'" is running in '+self.mode+' mode', on_irc=False)
		else:
			self.mode = None
			if self.irc_connection.really_connected == True:
				self.say('[Error] failed to connect to the XMPP room, leaving ...', on_xmpp=False)
			for error in errors:
				try:
					raise error
				except:
					traceback.print_exc()
			self.bot.error('[Error] failed to connect to the XMPP room of bridge "'+str(self)+'", removing bridge', send_to_admins=True)
			self.bot.removeBridge(self)
	
	
	def addParticipant(self, from_protocol, nickname):
		"""Add a participant to the bridge."""
		if (from_protocol == 'irc' and nickname == self.irc_connection.get_nickname()) or (from_protocol == 'xmpp' and nickname == self.xmpp_room.nickname):
			self.bot.error('===> Debug: not adding self ('+self.bot.nickname+') to bridge "'+str(self)+'"', debug=True)
			return
		try:
			p = self.getParticipant(nickname)
			if p.protocol != from_protocol:
				if from_protocol == 'irc' and isinstance(p.irc_connection, ServerConnection) and p.irc_connection.really_connected == True or from_protocol == 'xmpp' and isinstance(p.xmpp_c, xmpp.client.Client) and isinstance(p.muc, xmpp.muc):
					return
				self.bot.error('===> Debug: "'+nickname+'" is on both sides of bridge "'+str(self)+'"', debug=True)
				self.say('[Warning] The nickname "'+nickname+'" is used on both sides of the bridge, please avoid that if possible')
				if isinstance(p.irc_connection, ServerConnection):
					p.irc_connection.close('')
				if p.irc_connection != 'both':
					p.irc_connection = 'both'
				if isinstance(p.muc, xmpp.muc):
					p.muc.leave('')
					self.bot.close_xmpp_connection(p.nickname)
				if p.xmpp_c != 'both':
					p.xmpp_c = 'both'
			return
		except NoSuchParticipantException:
			pass
		self.lock.acquire()
		self.bot.error('===> Debug: adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'"', debug=True)
		try:
			p = participant(self, from_protocol, nickname)
		except IOError:
			self.bot.error('===> Debug: IOError while adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'", reconnectiong ...', debug=True)
			p.xmpp_c.reconnectAndReauth()
		except:
			self.bot.error('===> Debug: unknown error while adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'"', debug=True)
			traceback.print_exc()
			return
		self.participants.append(p)
		self.lock.release()
		if self.mode != 'normal' and from_protocol == 'xmpp':
			xmpp_participants_nicknames = self.get_participants_nicknames_list(protocols=['xmpp'])
			self.say('[Info] Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
		return p
	
	
	def getParticipant(self, nickname):
		"""Returns a participant object if there is a participant using nickname in the bridge. Raises a NoSuchParticipantException otherwise."""
		self.lock.acquire()
		for participant_ in self.participants:
			if participant_.nickname == nickname:
				self.lock.release()
				return participant_
		self.lock.release()
		raise NoSuchParticipantException('there is no participant using the nickname "'+nickname+'" in this bridge')
	
	
	def get_participants_nicknames_list(self, protocols=['irc', 'xmpp']):
		"""Returns a list of the nicknames of the bridge's participants that are connected on the XMPP side."""
		self.lock.acquire()
		participants_nicknames = []
		for p in self.participants:
			if p.protocol in protocols:
				participants_nicknames.append('"'+p.nickname+'"')
		self.lock.release()
		return participants_nicknames
	
	
	def hasParticipant(self, nickname):
		try:
			self.getParticipant(nickname)
			return True
		except NoSuchParticipantException:
			return False
	
	
	def removeParticipant(self, left_protocol, nickname, leave_message):
		"""Remove the participant using nickname from the bridge. Raises a NoSuchParticipantException if nickname is not used in the bridge."""
		
		was_on_both = None
		p = self.getParticipant(nickname)
		if p.protocol == 'xmpp':
			if p.irc_connection == 'both':
				was_on_both = True
				if left_protocol == 'xmpp':
					p.protocol = 'irc'
					p.createDuplicateOnXMPP()
				elif left_protocol == 'irc':
					p.createDuplicateOnIRC()
			else:
				if left_protocol == 'xmpp':
					was_on_both = False
				elif left_protocol == 'irc':
					try:
						p.irc_connection.join(self.irc_room)
					except:
						p._close_irc_connection()
						p.createDuplicateOnIRC()
					return
		
		elif p.protocol == 'irc':
			if p.xmpp_c == 'both':
				was_on_both = True
				if left_protocol == 'irc':
					p.protocol = 'xmpp'
					p.createDuplicateOnIRC()
				elif left_protocol == 'xmpp':
					p.createDuplicateOnXMPP()
			else:
				if left_protocol == 'irc':
					was_on_both = False
				elif left_protocol == 'xmpp':
					if isinstance(p.xmpp_c, xmpp.client.Client):
						self.bot.reopen_xmpp_connection(p.xmpp_c)
					return
		
		else:
			raise Exception('[Internal Error] bad protocol')
		
		if was_on_both == True:
			self.bot.error('===> Debug: "'+nickname+'" was on both sides of bridge "'+str(self)+'" but left '+left_protocol, debug=True)
		
		elif was_on_both == False:
			self.lock.acquire()
			self.bot.error('===> Debug: removing participant "'+nickname+'" from bridge "'+str(self)+'"', debug=True)
			self.participants.remove(p)
			p.leave(leave_message)
			del p
			self.lock.release()
			if left_protocol == 'xmpp':
				xmpp_participants_nicknames = self.get_participants_nicknames_list(protocols=['xmpp'])
				if self.irc_connections_limit != -1 and self.irc_connections_limit > len(xmpp_participants_nicknames):
					self.switchFromLimitedToNormalMode()
				if self.mode != 'normal':
					self.say('[Info] Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
			elif left_protocol == 'irc':
				if self.mode == 'minimal':
					irc_participants_nicknames = self.get_participants_nicknames_list(protocols=['irc'])
					self.say('[Info] Participants on IRC: '+'  '.join(irc_participants_nicknames), on_irc=False)
		
		else:
			self.bot.error('=> Debug: Bad decision tree,  p.protocol='+p.protocol+'  left_protocol='+left_protocol+'\np.xmpp_c='+str(p.xmpp_c)+'\np.irc_connection='+str(p.irc_connection), debug=True)
	
	
	def say(self, message, on_irc=True, on_xmpp=True):
		"""Make the bot say something."""
		if message[0] != '[':
			raise Exception('[Internal Error] message does not start with "["')
		if self.say_level == self.__class__._nothing:
			return
		level = re.findall('^\[(Info|Notice|Warning|Error)\]', message)
		if len(level) == 0:
			raise Exception('[Internal Error] unknown message importance "'+re.findall('^\[([^[\]]+)', message)[0]+'"')
		level = level[0].lower()
		if getattr(self.__class__, '_'+level) < self.say_level:
			return
		if on_xmpp == True:
			self.xmpp_room.say(message)
		if on_irc == True:
			self.irc_connection.privmsg(self.irc_room, message)
	
	
	def switchFromLimitedToNormalMode(self):
		if self.mode != 'normal-limited':
			return
		self.bot.error('===> Bridge is switching to normal mode.')
		self.say('[Notice] Bridge is switching to normal mode.')
		self.mode = 'normal'
		for p in self.participants:
			if p.protocol == 'xmpp':
				p.createDuplicateOnIRC()
	
	
	def switchFromNormalToLimitedMode(self):
		if self.mode != 'normal':
			return
		self.mode = 'normal-limited'
		i = 0
		for p in self.participants:
			if p.protocol == 'xmpp':
				i += 1
				if isinstance(self.irc_connection, ServerConnection):
					p.irc_connection.close('Bridge is switching to limited mode')
					p.irc_connection = None
		self.irc_connections_limit = i
		self.bot.error('===> Bridge is switching to limited mode. Limit seems to be '+str(self.irc_connections_limit)+' on "'+self.irc_server+'".')
		self.say('[Warning] Bridge is switching to limited mode, it means that it will be transparent for XMPP users but not for IRC users, this is due to the IRC servers\' per-IP-address connections\' limit number which seems to be '+str(self.irc_connections_limit)+' on "'+self.irc_server+'".')
		xmpp_participants_nicknames = self.get_participants_nicknames_list(protocols=['xmpp'])
		self.say('[Info] Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
	
	
	def __str__(self):
		return self.irc_room+'@'+self.irc_server+' <-> '+self.xmpp_room.room_jid
	
	
	def __del__(self):
		# Delete participants objects
		for p in self.participants:
			p.leave('Removing bridge')
			del p
		del self.participants
		
		# Close IRC connection if not used by an other bridge, just leave the room otherwise
		self.irc_connection.used_by -= 1
		if self.irc_connection.used_by < 1:
			self.irc_connection.close('Removing bridge')
		else:
			self.irc_connection.part(self.irc_room, message='Removing bridge')
		
		# Leave XMPP room
		self.xmpp_room.leave('Removing bridge')