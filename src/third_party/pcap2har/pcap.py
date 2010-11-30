import dpkt
from pcaputil import *
from socket import inet_ntoa

import logging as log
import os
import shutil
import tcp

class TCPFlowAccumulator:
    '''
    Takes a list of TCP packets and organizes them into distinct
    connections, or flows. It does this by organizing packets into a
    dictionary indexed by their socket (the tuple
    ((srcip, sport), (dstip,dport)), possibly the other way around).

    Members:
    flowdict = {socket: tcp.Flow}, the list of tcp.Flow's organized by socket
    '''
    def __init__(self, pcap_reader):
        '''
        scans the pcap_reader for TCP packets, and adds them to the tcp.Flow
        they belong to, based on their socket

        Args:
        pcap_reader = pcaputil.ModifiedReader
        errors = list of errors encountered during parsing pcap.
        '''
        self.flowdict = {}
        self.errors = []
        debug_pkt_count = 0
        try:
            for pkt in pcap_reader:
                debug_pkt_count += 1
                # discard incomplete packets
                header = pkt[2]
                if debug_pkt_count == 936:
                    pass
                if header.caplen != header.len:
                    # packet is too short
                    log.warning('discarding incomplete packet')
                    self.errors.append((pkt, 'packet is too short', debug_pkt_count))
                # parse packet
                try:
                    dltoff = dpkt.pcap.dltoff
                    if pcap_reader.dloff == dltoff[dpkt.pcap.DLT_LINUX_SLL]:
                        eth = dpkt.sll.SLL(pkt[1])
                    else:
                        # TODO(lsong): Check other packet type.
                        # Default is ethernet.
                        eth = dpkt.ethernet.Ethernet(pkt[1])
                    if isinstance(eth.data, dpkt.ip.IP):
                        ip = eth.data
                        if isinstance(ip.data, dpkt.tcp.TCP):
                            # then it's a TCP packet
                            # process it
                            tcppkt = tcp.Packet(pkt[0], pkt[1], eth, ip, ip.data)
                            self.process_packet(tcppkt) # organize by socket
                    # TODO(lsong): UDP packet for DNS lookup.
                except dpkt.Error as error:
                    log.warning(error)
                    self.errors.append((pkt, e, debug_pkt_count))
        except dpkt.dpkt.NeedData as error:
            log.warning(error)
            log.warning('A packet in the pcap file was too short, '
                        'debug_pkt_count=%d' % debug_pkt_count)
            self.errors.append((None, error))
        # finish all tcp flows
        map(tcp.Flow.finish, self.flowdict.itervalues())

    def process_packet(self, pkt):
        '''
        adds the tcp packet to flowdict. pkt is a TCPPacket
        '''
        #try both orderings of src/dst socket components
        #otherwise, start a new list for that socket
        src, dst = pkt.socket
        srcip, srcport = src
        dstip, dstport = dst
        if (srcport == 5223 or dstport == 5223):
            log.warning("hpvirtgrp packets are ignored.")
            return
        if (srcport == 5228 or dstport == 5228):
            log.warning("hpvroom packets are ignored.")
            return
        if (srcport == 443 or dstport == 443):
            log.warning("HTTPS packets are ignored.")
            return

        if (src, dst) in self.flowdict:
            #print '  adding as ', (src, dst)
            self.flowdict[(src,dst)].add(pkt)
        elif (dst, src) in self.flowdict:
            #print '  adding as ', (dst, src)
            self.flowdict[(dst, src)].add(pkt)
        else:
            #print '  making new dict entry as ', (src, dst)
            newflow = tcp.Flow()
            newflow.add(pkt)
            self.flowdict[(src,dst)] = newflow
    def flows(self):
        '''
        lists available flows by socket
        '''
        return [friendly_socket(s) for s in self.flowdict.keys()]

    def get_flow(self, **kwargs):
        '''
        Pick out a flow by criteria determined by kwargs. Return the first one
        that matches, along with its socket. If none match, return None. Meant
        for console use.

        Keyword argument values:
        socket = pick flow according to socket
        fwd = string, beginning of fwd data
        rev = string, beginning of reverse data
        sport = src port
        dport = dest port
        '''
        # a map of keywords to predicates
        # predicates take flow and value, and return whether the flow matches
        # the criterion
        predicates = {
            'socket': (lambda f, v: f.socket == v),
            'fwd': (lambda f, v: f.fwd.data.startswith(v)),
            'rev': (lambda f, v: f.rev.data.startswith(v)),
            'sport': (lambda f, v: f.socket[0][1] == v),
            'dport': (lambda f, v: f.socket[1][1] == v)
        }
        # look at each flow
        for flow in self.flowdict.itervalues():
            candidate = True
            # iter through kwargs, match with preds
            for k, v in kwargs.iteritems():
                # if the requested attribute is in our dict
                if k in predicates:
                    # if the predicate is false, rule out the flow
                    if not predicates[k](flow, kwargs[k]):
                        candidate = False
                        break
            # if all the predicates passed...
            if candidate:
                return flow

def TCPFlowsFromFile(filename):
    '''
    helper function for getting a TCPFlowAccumulator from a pcapfilename.
    Filename in, flows out. Intended to be used from the console.
    '''
    f = open(filename,'rb')
    reader = ModifiedReader(f)
    return TCPFlowAccumulator(reader)

def verify_file(filename):
    '''
    attempts to construct packets from all the packets in the file, to
    verify their validity, or dpkt's ability to interpret them. Intended to be
    used from the console.
    '''
    f = open(filename,'rb')
    reader = dpkt.pcap.Reader(f)
    i = 0
    for pkt in reader:
        try:
            eth = dpkt.ethernet.Ethernet(pkt[1])
        except dpkt.UnpackError:
            print 'error in packet #', i
            raise # let it hit the console
        i += 1

def WriteTCPFlowsFromFile(filename):
    '''
    takes a filename, parses the file with TCPFlowAccumulator, and writes the
    contents of all the flows to a directory "filename.d"
    '''
    flows = TCPFlowsFromFile(filename)
    output_dir = filename + ".d"
    # get clean directory
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.mkdir(output_dir)
    # write out data
    for i, f in enumerate(flows.flowdict.itervalues()):
        f.writeout_data(os.path.join(output_dir, str(i)))
