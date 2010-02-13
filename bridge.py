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
import threading
import traceback

from irclib import ServerConnection
import muc
xmpp = muc.xmpp
del muc

from participant import Participant
import say_levels


class Bridge:
	
	modes = ['bypass', 'normal', 'limited', 'minimal']
	
	class NoSuchParticipantException(Exception): pass
	
	
	def __init__(self, owner_bot, xmpp_room_jid, irc_room, irc_server, mode, say_level, irc_port=6667, irc_connection_interval=None, irc_charsets=None):
		"""Create a new bridge."""
		self.bot = owner_bot
		self.irc_server = irc_server
		self.irc_port = irc_port
		self.irc_room = irc_room.lower()
		self.irc_connection_interval = irc_connection_interval
		self.irc_charsets = irc_charsets
		self.xmpp_room_jid = xmpp_room_jid
		self.say_level = say_level
		self.participants = []
		if mode not in self.__class__.modes:
			raise Exception('[Error] "'+mode+'" is not a correct value for a bridge\'s "mode" attribute')
		self.mode = mode
		
		self.lock = threading.RLock()
		
		self.init2()
	
	
	def init2(self):
		# Join XMPP room
		self.xmpp_room = xmpp.muc(self.xmpp_room_jid)
		self.xmpp_room.join(self.bot.xmpp_c, self.bot.nickname, callback=self._xmpp_join_callback)
		
		# Join IRC room
		self.irc_connection = self.bot.irc.open_connection(self.irc_server, self.irc_port, self.bot.nickname, delay=self.irc_connection_interval)
		self.irc_connection.connect(nick_callback=self._irc_nick_callback, charsets=self.irc_charsets)
		
		self.bot.error(say_levels.notice, 'bridge "'+str(self)+'" is running in '+self.mode+' mode and a say_level of "'+str(self.say_level)+'"')
	
	
	def _join_irc_failed(self):
		self.bot.error(say_levels.error, 'failed to connect to the IRC chan of bridge "'+str(self)+'", stopping bridge', send_to_admins=True)
		self.stop(message='failed to connect to the IRC chan')
	
	
	def _irc_nick_callback(self, error, arguments=[]):
		if error == None:
			if self.mode == None:
				return
			self.irc_connection.join(self.irc_room)
			self.bot.error(3, 'successfully connected on IRC side of bridge "'+str(self)+'"', debug=True)
			self.say(say_levels.notice, 'bridge "'+str(self)+'" is running in '+self.mode+' mode', on_xmpp=False)
			if self.mode not in ['normal', 'bypass']:
				self.show_participants_list_on(protocols=['irc'])
		else:
			self.mode = None
			if self.xmpp_room.connected == True:
				self.say(say_levels.error, 'failed to connect to the IRC chan, leaving ...', on_irc=False)
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
					raise Exception('[Error] unknown error for "'+self.bot.nickname+'" on bridge "'+str(self)+'"')
			except:
				trace = traceback.format_exc()
			self._join_irc_failed()
	
	
	def _RemoteServerNotFound_handler(self):
		server = xmpp.protocol.JID(self.xmpp_room_jid).getDomain()
		bridges = self.bot.findBridges([server])
		error_message = '[Warning] The MUC server '+server+' seems to be down, the bot will try to recreate all bridges related to this server in 5 minutes'
		self.bot.restart_bridges_delayed(bridges, 300, error_message)
	
	
	def _xmpp_join_callback(self, errors):
		"""Called by muc._xmpp_presence_handler"""
		if len(errors) == 0:
			if hasattr(self, 'reconnecting'):
				del self.reconnecting
			if self.mode == None:
				return
			self.bot.error(3, 'succesfully connected on XMPP side of bridge "'+str(self)+'"', debug=True)
			self.say(say_levels.notice, 'bridge "'+str(self)+'" is running in '+self.mode+' mode', on_irc=False)
		else:
			self.mode = None
			if self.irc_connection.really_connected == True:
				self.say(say_levels.error, 'failed to connect to the XMPP room, leaving ...', on_xmpp=False)
			for error in errors:
				try:
					raise error
				except xmpp.muc.RemoteServerNotFound:
					self._RemoteServerNotFound_handler()
				except:
					trace = traceback.format_exc()
			self.bot.error(say_levels.error, 'failed to connect to the XMPP room of bridge "'+str(self)+'", stopping bridge\n'+trace, send_to_admins=True)
			self.stop(message='failed to connect to the XMPP room')
	
	
	def addParticipant(self, from_protocol, nickname, real_jid=None, irc_id=None):
		"""Add a participant to the bridge."""
		if (from_protocol == 'irc' and nickname == self.bot.nickname) or (from_protocol == 'xmpp' and nickname == self.bot.nickname):
			self.bot.error(3, 'not adding self ('+self.bot.nickname+') to bridge "'+str(self)+'"', debug=True)
			return
		try:
			p = self.getParticipant(nickname)
			if p.protocol != from_protocol:
				if from_protocol == 'irc' and isinstance(p.irc_connection, ServerConnection) and p.irc_connection.really_connected == True and p.irc_connection.real_nickname == nickname or from_protocol == 'xmpp' and isinstance(p.xmpp_c, xmpp.client.Client) and isinstance(p.muc, xmpp.muc) and p.xmpp_c.nickname == nickname:
					if irc_id:
						p.irc_connection.irc_id = irc_id
					return p
				p.set_both_sides()
			return p
		except self.NoSuchParticipantException:
			pass
		
		if nickname == 'ChanServ' and from_protocol == 'irc':
			return
		
		self.lock.acquire()
		self.bot.error(3, 'adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'"', debug=True)
		try:
			p = Participant(self, from_protocol, nickname, real_jid=real_jid)
		except IOError:
			self.bot.error(3, 'IOError while adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'", reconnectiong ...', debug=True)
			p.xmpp_c.reconnectAndReauth()
		except:
			self.bot.error(3, 'unknown error while adding participant "'+nickname+'" from "'+from_protocol+'" to bridge "'+str(self)+'"', debug=True)
			traceback.print_exc()
			return
		self.participants.append(p)
		self.lock.release()
		if self.mode not in ['normal', 'bypass']:
			if from_protocol == 'xmpp':
				self.show_participants_list_on(protocols=['irc'])
			elif self.mode == 'minimal' and from_protocol == 'irc':
				self.show_participants_list_on(protocols=['xmpp'])
		return p
	
	
	def createDuplicatesOn(self, protocols):
		for p in self.participants:
			if p.protocol == 'xmpp' and 'irc' in protocols:
				p.createDuplicateOnIRC()
			elif p.protocol == 'irc' and 'xmpp' in protocols:
				p.createDuplicateOnXMPP()
	
	
	def changeMode(self, new_mode):
		if new_mode == self.mode:
			return 'Mode is already equal to '+self.mode
		
		old_mode = self.mode
		self.mode = new_mode
		
		unhandled = False
		
		if new_mode in ['normal', 'bypass']:
			
			if old_mode == 'limited':
				# From  limited  to  {normal,bypass}
				self.createDuplicatesOn(['irc'])
			
			elif old_mode in ['minimal', 'normal']:
				# From  {minimal,normal}  to  {normal,bypass}
				self.createDuplicatesOn(['irc', 'xmpp'])
			
			elif old_mode == 'bypass':
				# From  bypass  to  normal
				pass  # Handled below
			
			else:
				# Unhandled mode changing
				unhandled = True
			
		elif new_mode == 'limited':
			
			if old_mode == 'minimal':
				self.createDuplicatesOn(['xmpp'])
			
			i = 0
			for p in self.participants:
				if p.protocol == 'xmpp':
					i += 1
					p._close_irc_connection('Bridge is switching to limited mode')
			
			self.show_participants_list_on(protocols=['irc'])
		
		elif new_mode == 'minimal':
			for p in self.participants:
				p.leave('Bridge is switching to minimal mode')
		
		else:
			# Unhandled mode changing
			unhandled = True
		
		if unhandled:
			self.mode = old_mode
			return 'Error: unhandled mode changing from '+self.mode+' to '+new_mode
		
		if old_mode == 'bypass':
			# From  bypass  to  *
			for p in self.participants:
				if p.nickname != p.duplicate_nickname:
					p.leave('Bridge is switching to '+new_mode+' mode')
		
		self.say(say_levels.notice, 'Bridge is switching from '+old_mode+' to '+new_mode+' mode.', log=True)
	
	
	def getParticipant(self, nickname):
		"""Returns a participant object if there is a participant using nickname in the bridge. Raises a NoSuchParticipantException otherwise."""
		self.lock.acquire()
		for p in self.participants:
			if nickname in [p.nickname, p.duplicate_nickname]:
				self.lock.release()
				return p
		self.lock.release()
		raise self.NoSuchParticipantException('there is no participant using the nickname "'+nickname+'" in this bridge')
	
	
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
		except self.NoSuchParticipantException:
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
					if isinstance(p.irc_connection, ServerConnection):
						p.irc_connection.join(self.irc_room)
					else:
						c = self.bot.irc.get_connection(self.irc_server, self.irc_port, p.duplicate_nickname)
						if not (c and self.irc_room in c.left_channels):
							p._close_irc_connection(leave_message)
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
			self.bot.error(3, '"'+nickname+'" was on both sides of bridge "'+str(self)+'" but left '+left_protocol, debug=True)
		
		elif was_on_both == False:
			self.lock.acquire()
			self.bot.error(3, 'removing participant "'+nickname+'" from bridge "'+str(self)+'"', debug=True)
			self.participants.remove(p)
			p.leave(leave_message)
			del p
			self.lock.release()
			if left_protocol == 'xmpp':
				if self.mode not in ['normal', 'bypass']:
					self.show_participants_list_on(protocols=['irc'])
			elif left_protocol == 'irc':
				if self.mode == 'minimal':
					self.show_participants_list_on(protocols=['xmpp'])
		
		else:
			self.bot.error(1, 'Bad decision tree,  p.protocol='+p.protocol+'  left_protocol='+left_protocol+'\np.xmpp_c='+str(p.xmpp_c)+'\np.irc_connection='+str(p.irc_connection), debug=True)
	
	
	def restart(self):
		"""Restart the bridge"""
		
		# Tell admins
		self.bot.error(-1, 'Restarting bridge '+str(self), send_to_admins=True)
		
		# Stop the bridge
		self.stop(message='Restarting bridge')
		
		# Recreate the bridge
		self.init2()
	
	
	def say(self, importance, message, on_irc=True, on_xmpp=True, log=False, send_to_admins=False):
		"""Make the bot say something."""
		message = self.bot.format_message(importance, message)
		if log or send_to_admins:
			self.bot.error(importance, message+' ('+str(self)+')', send_to_admins=send_to_admins)
		if importance < self.say_level:
			return
		if on_xmpp == True:
			self.xmpp_room.say(message)
		if on_irc == True:
			self.irc_connection.privmsg(self.irc_room, message)
	
	
	def show_participants_list_on(self, protocols=[]):
		if 'irc' in protocols and self.irc_connection.really_connected:
			xmpp_participants_nicknames = self.get_participants_nicknames_list(protocols=['xmpp'])
			self.say(say_levels.info, 'Participants on XMPP: '+'  '.join(xmpp_participants_nicknames), on_xmpp=False)
		if 'xmpp' in protocols:
			irc_participants_nicknames = self.get_participants_nicknames_list(protocols=['irc'])
			self.say(say_levels.info, 'Participants on IRC: '+'  '.join(irc_participants_nicknames), on_irc=False)
	
	
	def stop(self, message='Stopping bridge'):
		"""Stop the bridge"""
		
		# Close IRC connection if not used by an other bridge, just leave the room otherwise
		if isinstance(self.irc_connection, ServerConnection):
			self.irc_connection.used_by -= 1
			if self.irc_connection.used_by < 1:
				self.irc_connection.close(message)
			else:
				self.irc_connection.part(self.irc_room, message=message)
			self.irc_connection = None
		
		# Leave the MUC
		if isinstance(self.xmpp_room, xmpp.muc):
			self.xmpp_room.leave(message=message)
			self.xmpp_room.__del__()
			self.xmpp_room = None
		
		# Delete participants objects
		for p in self.participants:
			p.leave(message)
		self.participants = []
	
	
	def __str__(self):
		return self.irc_room+'@'+self.irc_server+' <-> '+self.xmpp_room_jid
	
	
	def __del__(self):
		self.stop(message='Removing bridge')
